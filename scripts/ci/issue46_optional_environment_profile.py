"""Fail-closed #46 conformance reports for optional MCP and LiteLLM environments."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    ConformanceReport,
    ConformanceScenario,
    run_conformance,
)
from ai_multi_agent_platform.conformance.mcp_protocol import (
    MCPProtocolEvidence,
    combine_mcp_compatibility,
    missing_mcp_protocol_evidence,
)


@dataclass(frozen=True, slots=True)
class OptionalEnvironmentProfile:
    name: str
    scenario_id: str
    owner: str
    criterion: str
    deployment_profile: str
    distribution: str
    component_name: str
    expected_version: str
    pytest_node: str


_PROFILES: dict[str, OptionalEnvironmentProfile] = {
    "mcp": OptionalEnvironmentProfile(
        name="mcp",
        scenario_id="ENV-MCP",
        owner="#12 MCP capability adapter",
        criterion=(
            "the pinned official MCP Python SDK stdio transport executes a canonical "
            "CapabilityInvocation through CapabilityRegistry/CapabilityInvoker"
        ),
        deployment_profile="mcp-sdk-pinned",
        distribution="mcp",
        component_name="mcp-python-sdk",
        expected_version="2.1.1",
        pytest_node=(
            "tests/test_mcp_sdk_transport.py::"
            "test_official_mcp_sdk_stdio_transport_uses_canonical_invocation_path"
        ),
    ),
    "litellm": OptionalEnvironmentProfile(
        name="litellm",
        scenario_id="ENV-LITELLM",
        owner="#11 LiteLLM model adapter",
        criterion=(
            "the pinned real LiteLLM library executes behind the canonical ModelProvider "
            "adapter without requiring a paid provider or credential"
        ),
        deployment_profile="litellm-pinned",
        distribution="litellm",
        component_name="litellm",
        expected_version="1.99.0",
        pytest_node=(
            "tests/integration/upstreams/test_litellm_pinned.py::"
            "test_pinned_litellm_library_executes_through_platform_adapter"
        ),
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=sorted(_PROFILES))
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository checkout containing the maintained acceptance test.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Destination for the machine-readable #46 conformance report.",
    )
    parser.add_argument(
        "--protocol-evidence",
        type=Path,
        help="Official MCP protocol-conformance evidence to combine with ENV-MCP.",
    )
    parser.add_argument(
        "--compatibility-report",
        type=Path,
        help="Destination for the separate protocol/platform MCP compatibility report.",
    )
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def _probe(profile: OptionalEnvironmentProfile) -> int:
    try:
        installed_version = metadata.version(profile.distribution)
    except metadata.PackageNotFoundError:
        print(
            f"{profile.component_name} profile requires {profile.distribution}=="
            f"{profile.expected_version}; distribution is not installed",
            file=sys.stderr,
        )
        return 2
    if installed_version != profile.expected_version:
        print(
            f"{profile.component_name} profile requires exact version "
            f"{profile.expected_version}; got {installed_version}",
            file=sys.stderr,
        )
        return 2
    return subprocess.run(
        (sys.executable, "-m", "pytest", "-q", profile.pytest_node),
        check=False,
    ).returncode


def run_profile(
    profile: OptionalEnvironmentProfile,
    *,
    repository_root: Path,
) -> ConformanceReport:
    script = Path(__file__).resolve()
    scenario = ConformanceScenario(
        scenario_id=profile.scenario_id,
        owner=profile.owner,
        criterion=profile.criterion,
        command=(sys.executable, str(script), profile.name, "--probe"),
        required=True,
    )
    return run_conformance(
        ConformanceProfile.INTEGRATION,
        repository_root=repository_root,
        deployment_profile=profile.deployment_profile,
        adapter_versions={profile.component_name: profile.expected_version},
        scenarios=(scenario,),
    )


def _write_mcp_compatibility(
    *,
    report: ConformanceReport,
    protocol_evidence_path: Path | None,
    destination: Path,
) -> bool:
    if protocol_evidence_path is None:
        compatibility = missing_mcp_protocol_evidence(platform_conformant=report.passed)
    else:
        protocol = MCPProtocolEvidence.from_json(
            protocol_evidence_path.read_text(encoding="utf-8")
        )
        compatibility = combine_mcp_compatibility(
            protocol,
            platform_conformant=report.passed,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(compatibility.to_json(), encoding="utf-8")
    return compatibility.compatible


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = _PROFILES[args.profile]
    if args.probe:
        return _probe(profile)

    if args.protocol_evidence is not None and profile.name != "mcp":
        print("--protocol-evidence is only valid for the MCP profile", file=sys.stderr)
        return 2
    if args.compatibility_report is not None and profile.name != "mcp":
        print("--compatibility-report is only valid for the MCP profile", file=sys.stderr)
        return 2

    report = run_profile(profile, repository_root=args.repository_root)
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report.to_json(), encoding="utf-8")

    compatibility_ok: bool | None = None
    if args.compatibility_report is not None:
        try:
            compatibility_ok = _write_mcp_compatibility(
                report=report,
                protocol_evidence_path=args.protocol_evidence,
                destination=args.compatibility_report,
            )
        except (OSError, ValueError) as exc:
            print(f"invalid MCP protocol evidence: {exc}", file=sys.stderr)
            return 2

    print(report.human_summary())
    if not report.passed:
        return 1
    if compatibility_ok is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
