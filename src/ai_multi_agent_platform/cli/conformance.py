"""CLI for the platform-wide M3 conformance gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    activate_optional_scenarios,
    run_conformance,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-conformance")
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in ConformanceProfile],
        default=ConformanceProfile.FAST.value,
        help="Conformance tier to run.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository checkout containing canonical tests and frontend sources.",
    )
    parser.add_argument(
        "--deployment-profile",
        help="Deployment/configuration profile name recorded in the report.",
    )
    parser.add_argument(
        "--enable-optional",
        action="append",
        default=[],
        metavar="SCENARIO_ID",
        help=(
            "Explicitly enable an optional scenario for this compatibility claim. "
            "May be repeated or use comma-separated IDs."
        ),
    )
    parser.add_argument(
        "--adapter-version",
        action="append",
        default=[],
        metavar="NAME=VERSION",
        help="Record a tested adapter/runtime version. May be repeated.",
    )
    parser.add_argument(
        "--provider-version",
        action="append",
        default=[],
        metavar="NAME=VERSION",
        help="Record a tested provider version. May be repeated.",
    )
    parser.add_argument(
        "--plugin-version",
        action="append",
        default=[],
        metavar="NAME=VERSION",
        help="Record a tested plugin version. May be repeated.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional destination for the machine-readable conformance report.",
    )
    return parser


def _parse_optional(values: list[str]) -> tuple[str, ...]:
    return tuple(
        item.strip().upper() for value in values for item in value.split(",") if item.strip()
    )


def _parse_component_versions(values: list[str], option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        name, separator, component_version = value.partition("=")
        name = name.strip()
        component_version = component_version.strip()
        if not separator or not name or not component_version:
            raise ValueError(f"{option} requires NAME=VERSION; got {value!r}")
        if name in parsed:
            raise ValueError(f"{option} repeats component name {name!r}")
        parsed[name] = component_version
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profile = ConformanceProfile(args.profile)
    try:
        enabled_optional = _parse_optional(args.enable_optional)
        scenarios = activate_optional_scenarios(profile, enabled_optional)
        adapter_versions = _parse_component_versions(args.adapter_version, "--adapter-version")
        provider_versions = _parse_component_versions(args.provider_version, "--provider-version")
        plugin_versions = _parse_component_versions(args.plugin_version, "--plugin-version")
    except ValueError as exc:
        parser.error(str(exc))

    report = run_conformance(
        profile,
        repository_root=args.repository_root,
        deployment_profile=args.deployment_profile,
        adapter_versions=adapter_versions,
        provider_versions=provider_versions,
        plugin_versions=plugin_versions,
        scenarios=scenarios,
    )
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report.to_json(), encoding="utf-8")
    print(report.human_summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
