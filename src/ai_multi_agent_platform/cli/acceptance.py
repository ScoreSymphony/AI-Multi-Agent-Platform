"""CLI for the single-node usable-prototype acceptance gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_multi_agent_platform.acceptance import AcceptanceProfile, run_acceptance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-acceptance")
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in AcceptanceProfile],
        default=AcceptanceProfile.REFERENCE.value,
        help="Acceptance profile to run.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository checkout containing the canonical tests and frontend.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional destination for the machine-readable acceptance report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = AcceptanceProfile(args.profile)
    report = run_acceptance(profile, repository_root=args.repository_root)
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report.to_json(), encoding="utf-8")
    print(report.human_summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
