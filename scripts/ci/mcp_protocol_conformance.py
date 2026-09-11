"""Run pinned official MCP conformance scenarios and emit machine-readable evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from ai_multi_agent_platform.conformance.mcp_protocol import (
    MCP_PROTOCOL_EVIDENCE_SCHEMA,
    MCPProtocolEvidence,
    MCPProtocolScenarioEvidence,
    MCPProtocolScenarioStatus,
    load_mcp_conformance_pins,
    sha256_text,
)

_DEFAULT_PIN_MANIFEST = Path("conformance/mcp/pins.json")
_TRACK_PROTOCOL_ENV = "AI_MULTI_AGENT_PLATFORM_MCP_CONFORMANCE_PROTOCOL_REVISION"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an exact pinned official MCP protocol-conformance track."
    )
    parser.add_argument("--track", choices=("stable", "prerelease"), required=True)
    parser.add_argument(
        "--suite-root",
        type=Path,
        required=True,
        help="Checkout of modelcontextprotocol/conformance at the pinned commit.",
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--pin-manifest", type=Path, default=_DEFAULT_PIN_MANIFEST)
    parser.add_argument("--platform-commit")
    parser.add_argument("--platform-release")
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero for a failing informational/non-gating track too.",
    )
    return parser


def _package_version(suite_root: Path) -> str:
    package_path = suite_root / "package.json"
    raw = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("official MCP conformance package.json must be an object")
    value = raw.get("version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("official MCP conformance package.json has no version")
    return value.strip()


def _git_head(path: Path) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), "rev-parse", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "cannot verify official MCP conformance checkout commit: "
            + (completed.stderr.strip() or "git rev-parse failed")
        )
    return completed.stdout.strip()


def _platform_commit(repository_root: Path) -> str | None:
    completed = subprocess.run(
        ("git", "-C", str(repository_root), "rev-parse", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _client_command(repository_root: Path) -> str:
    argv = [sys.executable, str(repository_root / "scripts/ci/mcp_conformance_client.py")]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _diagnostics(stdout: str, stderr: str, *, limit: int = 4000) -> str | None:
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if not combined:
        return None
    return combined[-limit:]


def _scenario_slug(scenario: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_" for character in scenario
    )


def _result_directories(results_root: Path) -> set[Path]:
    if not results_root.is_dir():
        return set()
    return {path.resolve() for path in results_root.iterdir() if path.is_dir()}


def _retain_scenario_artifacts(
    *,
    scenario: str,
    completed: subprocess.CompletedProcess[str],
    artifact_root: Path,
    official_results_root: Path,
    results_before: set[Path],
) -> tuple[str, str]:
    scenario_root = artifact_root / _scenario_slug(scenario)
    scenario_root.mkdir(parents=True, exist_ok=True)
    stdout_path = scenario_root / "runner.stdout.txt"
    stderr_path = scenario_root / "runner.stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")

    created_results = _result_directories(official_results_root) - results_before
    if created_results:
        # A single-scenario runner normally creates one directory. If upstream creates more,
        # retain all of them rather than guessing which detail file is authoritative.
        official_destination = scenario_root / "official-results"
        official_destination.mkdir(parents=True, exist_ok=True)
        for result_dir in sorted(created_results, key=lambda path: path.name):
            shutil.copytree(
                result_dir,
                official_destination / result_dir.name,
                dirs_exist_ok=True,
            )

    return str(stdout_path), str(stderr_path)


def run_track(args: argparse.Namespace) -> MCPProtocolEvidence:
    repository_root = args.repository_root.resolve()
    manifest_path = args.pin_manifest
    if not manifest_path.is_absolute():
        manifest_path = repository_root / manifest_path
    pins = load_mcp_conformance_pins(manifest_path)
    track = pins.track(args.track)
    suite_root = args.suite_root.resolve()

    actual_version = _package_version(suite_root)
    expected_version = track.suite_version.removeprefix("v")
    if actual_version != expected_version:
        raise RuntimeError(
            "official MCP conformance suite version drift: "
            f"expected {expected_version}, got {actual_version}"
        )
    actual_commit = _git_head(suite_root)
    if actual_commit != track.suite_commit:
        raise RuntimeError(
            "official MCP conformance suite commit drift: "
            f"expected {track.suite_commit}, got {actual_commit}"
        )

    suite_cli = suite_root / "dist" / "index.js"
    if not suite_cli.is_file():
        raise RuntimeError(
            "official MCP conformance suite is not built; expected dist/index.js. "
            "Run npm ci and npm run build in the pinned checkout first."
        )

    artifact_root = args.json_report.parent / f"{args.json_report.stem}-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    official_results_root = repository_root / "results"

    scenario_results: list[MCPProtocolScenarioEvidence] = []
    conformance_env = {
        **os.environ,
        _TRACK_PROTOCOL_ENV: track.protocol_revision,
    }
    for scenario in track.scenarios:
        command = (
            "node",
            str(suite_cli),
            track.mode,
            "--command",
            _client_command(repository_root),
            "--spec-version",
            track.protocol_revision,
            "--scenario",
            scenario,
        )
        results_before = _result_directories(official_results_root)
        completed = subprocess.run(
            command,
            cwd=repository_root,
            env=conformance_env,
            check=False,
            capture_output=True,
            text=True,
        )
        stdout_artifact, stderr_artifact = _retain_scenario_artifacts(
            scenario=scenario,
            completed=completed,
            artifact_root=artifact_root,
            official_results_root=official_results_root,
            results_before=results_before,
        )
        status = (
            MCPProtocolScenarioStatus.PASS
            if completed.returncode == 0
            else MCPProtocolScenarioStatus.FAIL
        )
        scenario_results.append(
            MCPProtocolScenarioEvidence(
                scenario_id=scenario,
                status=status.value,
                exit_code=completed.returncode,
                diagnostics=(
                    None
                    if status is MCPProtocolScenarioStatus.PASS
                    else _diagnostics(completed.stdout, completed.stderr)
                ),
                stdout_sha256=sha256_text(completed.stdout),
                stderr_sha256=sha256_text(completed.stderr),
                stdout_artifact=stdout_artifact,
                stderr_artifact=stderr_artifact,
            )
        )

    resolved_platform_commit = args.platform_commit or _platform_commit(repository_root)
    platform_release = args.platform_release or _installed_version("ai-multi-agent-platform")
    evidence = MCPProtocolEvidence(
        schema=MCP_PROTOCOL_EVIDENCE_SCHEMA,
        platform_commit=resolved_platform_commit,
        platform_release=platform_release,
        adapter_revision=resolved_platform_commit or "working-tree",
        sdk_version=_installed_version("mcp"),
        protocol_revision=track.protocol_revision,
        suite_repository=pins.upstream_repository,
        suite_version=track.suite_version,
        suite_commit=track.suite_commit,
        mode=track.mode,
        transport_profile=track.transport_profile,
        claimed=track.claimed,
        gating=track.gating,
        protocol_conformant=all(
            result.status == MCPProtocolScenarioStatus.PASS.value for result in scenario_results
        ),
        timestamp_utc=datetime.now(UTC).isoformat(),
        environment={
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "machine": platform.machine() or "unknown",
        },
        scenarios=tuple(scenario_results),
    )
    evidence.validate()
    return evidence


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence = run_track(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"MCP protocol conformance setup failed: {exc}", file=sys.stderr)
        return 2

    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(evidence.to_json(), encoding="utf-8")
    print(
        "MCP protocol conformance "
        f"[{args.track}/{evidence.protocol_revision}]: "
        f"{'PASS' if evidence.protocol_conformant else 'FAIL'} "
        f"({'claimed/gating' if evidence.gating else 'informational/not-claimed'})"
    )
    if evidence.protocol_conformant:
        return 0
    return 1 if evidence.gating or args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
