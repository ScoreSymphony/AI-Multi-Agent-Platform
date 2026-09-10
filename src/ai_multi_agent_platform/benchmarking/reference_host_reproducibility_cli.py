"""CLI for same-host reference campaign reproducibility analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from .reference_host_reproducibility import ReferenceHostReproducibilityAnalyzer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-reference-host-reproducibility")
    parser.add_argument(
        "--campaign-dir",
        action="append",
        required=True,
        type=Path,
        help="reference-host campaign output directory; repeat for each independent run",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    campaign_dirs = tuple(cast(list[Path], args.campaign_dir))
    output = cast(Path, args.output)
    try:
        report = ReferenceHostReproducibilityAnalyzer().analyze(
            campaign_dirs=campaign_dirs,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"reference-host reproducibility analysis failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
