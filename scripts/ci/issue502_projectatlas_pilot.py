#!/usr/bin/env python3
"""Run the pinned #502 ProjectAtlas pilot without granting repository ownership.

The harness deliberately does not install ProjectAtlas or call ``projectatlas init``. The caller
supplies an already verified executable. ProjectAtlas receives a read-only fixture repository and a
separate writable state directory, plus a deliberately minimal environment with no platform or CI
secrets. The report is suitable as reproducible evaluation evidence, not as an adoption decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any

PINNED_VERSION = "0.4.5"
_MAX_OUTPUT_BYTES = 2_000_000
_COMMAND_TIMEOUT_SECONDS = 180


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        digest.update(str(relative).encode("utf-8"))
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        elif path.is_dir():
            digest.update(b"dir\0")
    return digest.hexdigest()


def _provider_environment(state_dir: Path) -> dict[str, str]:
    home = state_dir / "home"
    cache = state_dir / "xdg-cache"
    config = state_dir / "xdg-config"
    data = state_dir / "xdg-data"
    for path in (home, cache, config, data):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(config),
        "XDG_DATA_HOME": str(data),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _make_fixture(root: Path) -> str:
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text(
        "# Repository intelligence pilot\n\nneedle appears in the implementation.\n",
        encoding="utf-8",
    )
    (root / "src" / "demo.py").write_text(
        "def selected_function() -> str:\n"
        "    return 'Needle from exact source'\n\n"
        "def caller() -> str:\n"
        "    return selected_function()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Issue 502 Pilot",
            "-c",
            "user.email=issue502@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(revision) not in {40, 64}:
        raise RuntimeError("fixture did not produce an immutable Git revision")
    return revision


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    mode = root.stat().st_mode
    root.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _restore_writable(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        try:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        except FileNotFoundError:
            pass


def _json_payload(stdout: str, command: str) -> Any:
    if len(stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise RuntimeError(f"{command} exceeded bounded stdout size")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{command} did not return JSON output: {stdout[:500]!r}") from exc


def _run_projectatlas(
    *,
    binary: Path,
    source_root: Path,
    database: Path,
    environment: dict[str, str],
    args: tuple[str, ...],
) -> tuple[Any, dict[str, float | int | str]]:
    command = [
        str(binary),
        "--require-version",
        PINNED_VERSION,
        "--db",
        str(database),
        "--format",
        "json",
        *args,
    ]
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        cwd=source_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ProjectAtlas command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout={completed.stdout[:1000]!r}\nstderr={completed.stderr[:1000]!r}"
        )
    payload = _json_payload(completed.stdout, " ".join(args))
    return payload, {
        "command": " ".join(args),
        "elapsed_ms": elapsed_ms,
        "stdout_bytes": len(completed.stdout.encode("utf-8")),
        "stderr_bytes": len(completed.stderr.encode("utf-8")),
        "user_cpu_seconds": max(0.0, usage_after.ru_utime - usage_before.ru_utime),
        "system_cpu_seconds": max(0.0, usage_after.ru_stime - usage_before.ru_stime),
        "max_rss_kb": int(usage_after.ru_maxrss),
    }


def _contains(payload: Any, text: str) -> bool:
    return text.casefold() in json.dumps(payload, ensure_ascii=False).casefold()


def run_pilot(binary: Path) -> dict[str, Any]:
    binary = binary.resolve()
    if not binary.is_file():
        raise FileNotFoundError(binary)

    version = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
    ).stdout.strip()
    if PINNED_VERSION not in version:
        raise RuntimeError(f"unexpected ProjectAtlas runtime: {version!r}")

    with tempfile.TemporaryDirectory(prefix="issue502-projectatlas-") as temporary:
        root = Path(temporary)
        source_root = root / "source"
        state_dir = root / "provider-state"
        source_root.mkdir()
        state_dir.mkdir()
        database = state_dir / "projectatlas.db"
        environment = _provider_environment(state_dir)
        revision = _make_fixture(source_root)
        source_digest_before = _tree_digest(source_root)
        _make_read_only(source_root)

        measurements: list[dict[str, float | int | str]] = []
        outputs: dict[str, Any] = {}
        try:
            for name, args in (
                ("scan", ("scan", ".")),
                ("search", ("search", "needle", "--limit", "10")),
                (
                    "slice",
                    ("slice", "src/demo.py", "--start-line", "2", "--end-line", "2"),
                ),
                ("health", ("health-check", "--summary-only")),
                ("settings", ("settings",)),
            ):
                payload, measurement = _run_projectatlas(
                    binary=binary,
                    source_root=source_root,
                    database=database,
                    environment=environment,
                    args=args,
                )
                outputs[name] = payload
                measurements.append(measurement)
        finally:
            _restore_writable(source_root)

        source_digest_after = _tree_digest(source_root)
        if source_digest_after != source_digest_before:
            raise RuntimeError("ProjectAtlas modified canonical source during the contained pilot")
        if (source_root / ".projectatlas").exists():
            raise RuntimeError("ProjectAtlas created project-local state despite explicit external DB")
        if not database.is_file() or database.stat().st_size <= 0:
            raise RuntimeError("ProjectAtlas did not create its external derived-state database")
        if not _contains(outputs["search"], "needle") or not _contains(
            outputs["search"], "src/demo.py"
        ):
            raise RuntimeError("ProjectAtlas search did not return the expected bounded source hit")
        if not _contains(outputs["slice"], "Needle from exact source"):
            raise RuntimeError("ProjectAtlas slice did not return the expected exact source")

        state_bytes = sum(path.stat().st_size for path in state_dir.rglob("*") if path.is_file())
        return {
            "schema_version": "1",
            "candidate": "ProjectAtlas",
            "candidate_version": PINNED_VERSION,
            "runtime_version": version,
            "fixture_revision": revision,
            "source_read_only_during_provider_execution": True,
            "source_unchanged": True,
            "project_local_state_created": False,
            "provider_database_outside_source": True,
            "provider_state_bytes": state_bytes,
            "network_isolation_verified": False,
            "network_isolation_note": (
                "This functional harness strips secrets but does not itself create a network namespace. "
                "Egress denial remains a separate containment gate before adoption."
            ),
            "commands": measurements,
            "checks": {
                "scan_json": isinstance(outputs["scan"], (dict, list)),
                "search_expected_hit": True,
                "slice_exact_source": True,
                "health_json": isinstance(outputs["health"], (dict, list)),
                "settings_json": isinstance(outputs["settings"], (dict, list)),
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    report = run_pilot(args.binary)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
