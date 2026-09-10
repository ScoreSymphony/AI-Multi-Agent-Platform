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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = _PROFILES[args.profile]
    if args.probe:
        return _probe(profile)

    report = run_profile(profile, repository_root=args.repository_root)
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report.to_json(), encoding="utf-8")
    print(report.human_summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
