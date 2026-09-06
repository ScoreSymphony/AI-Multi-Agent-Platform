"""CLI for the platform-wide M3 conformance gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_multi_agent_platform.conformance import ConformanceProfile, run_conformance


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
        "--json-report",
        type=Path,
        help="Optional destination for the machine-readable conformance report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = ConformanceProfile(args.profile)
    report = run_conformance(
        profile,
        repository_root=args.repository_root,
        deployment_profile=args.deployment_profile,
    )
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report.to_json(), encoding="utf-8")
    print(report.human_summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
