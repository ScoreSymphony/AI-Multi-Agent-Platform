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
    MCPPlatformProfileEvidence,
    MCPProfileIdentity,
    MCPProtocolEvidence,
    combine_mcp_compatibility,
    missing_mcp_protocol_evidence,
    not_claimed_mcp_profile,
    unsupported_mcp_profile,
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
    protocol_revision: str | None = None
    protocol_mode: str | None = None
    transport_profile: str | None = None
    claimed_protocol_profile: bool = False


_PROFILES: dict[str, OptionalEnvironmentProfile] = {
    "mcp": OptionalEnvironmentProfile(
        name="mcp",
        scenario_id="ENV-MCP",
        owner="#12 MCP capability adapter",
        criterion=(
            "the pinned official MCP Python SDK executes the exact 2025-11-25 client/tool "
            "Streamable-HTTP profile through CapabilityRegistry/CapabilityInvoker"
        ),
        deployment_profile="mcp-2025-11-25-client-streamable-http",
        distribution="mcp",
        component_name="mcp-python-sdk",
        expected_version="2.1.1",
        pytest_node=(
            "tests/integration/mcp/test_sdk_transports.py::"
            "test_official_mcp_sdk_streamable_http_uses_exact_claimed_profile"
        ),
        protocol_revision="2025-11-25",
        protocol_mode="client",
        transport_profile="streamable-http",
        claimed_protocol_profile=True,
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
        help="Destination for the separate protocol/platform MCP compatibility matrix.",
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


def _mcp_platform_evidence(
    profile: OptionalEnvironmentProfile,
    report: ConformanceReport,
) -> MCPPlatformProfileEvidence:
    if (
        profile.protocol_revision is None
        or profile.protocol_mode is None
        or profile.transport_profile is None
    ):
        raise ValueError("MCP environment profile is missing compatibility identity")
    return MCPPlatformProfileEvidence(
        identity=MCPProfileIdentity(
            protocol_revision=profile.protocol_revision,
            mode=profile.protocol_mode,
            transport_profile=profile.transport_profile,
        ),
        deployment_profile=report.deployment_profile,
        platform_commit=report.platform_commit,
        platform_release=report.platform_release,
        platform_conformant=report.passed,
        claimed=profile.claimed_protocol_profile,
    )


def _non_claimed_matrix_entries() -> tuple:
    return (
        not_claimed_mcp_profile(
            protocol_revision="2025-11-25",
            mode="client",
            transport_profile="stdio",
            reason=(
                "STDIO is retained as separate platform transport coverage; no official "
                "wire-conformance compatibility claim is attached to it"
            ),
        ),
        not_claimed_mcp_profile(
            protocol_revision="2026-07-28",
            mode="client",
            transport_profile="streamable-http-stateless",
            reason=(
                "implemented and protocol-tested, but intentionally not claimed while the "
                "official conformance line remains prerelease"
            ),
        ),
        unsupported_mcp_profile(
            protocol_revision="2025-11-25",
            mode="server",
            transport_profile="streamable-http",
            reason="the platform currently exposes no MCP server product surface",
        ),
    )


def _write_mcp_compatibility(
    *,
    profile: OptionalEnvironmentProfile,
    report: ConformanceReport,
    protocol_evidence_path: Path | None,
    destination: Path,
) -> bool:
    platform = _mcp_platform_evidence(profile, report)
    additional_profiles = _non_claimed_matrix_entries()
    if protocol_evidence_path is None:
        compatibility = missing_mcp_protocol_evidence(
            platform=platform,
            additional_profiles=additional_profiles,
        )
    else:
        protocol = MCPProtocolEvidence.from_json(protocol_evidence_path.read_text(encoding="utf-8"))
        compatibility = combine_mcp_compatibility(
            protocol,
            platform=platform,
            additional_profiles=additional_profiles,
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
                profile=profile,
                report=report,
                protocol_evidence_path=args.protocol_evidence,
                destination=args.compatibility_report,
            )
        except (OSError, ValueError) as exc:
            print(f"invalid MCP compatibility evidence: {exc}", file=sys.stderr)
            return 2

    print(report.human_summary())
    if not report.passed:
        return 1
    if compatibility_ok is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
