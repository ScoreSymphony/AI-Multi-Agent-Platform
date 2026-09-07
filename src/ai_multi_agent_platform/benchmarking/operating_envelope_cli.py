"""CLI for deriving comparable tested operating envelopes from benchmark evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .operating_envelope import OperatingEnvelopeAnalyzer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-operating-envelope")
    parser.add_argument(
        "--sweep",
        type=Path,
        action="append",
        required=True,
        help="single-node sweep summary JSON; repeat for comparable runs",
    )
    parser.add_argument(
        "--endurance",
        type=Path,
        action="append",
        default=[],
        help="single-node soak report JSON; repeat for comparable runs",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sweep_paths = cast(list[Path], args.sweep)
    endurance_paths = cast(list[Path], args.endurance)
    output = cast(Path, args.output)
    try:
        sweep_reports = tuple(_load_json_object(path) for path in sweep_paths)
        endurance_reports = tuple(_load_json_object(path) for path in endurance_paths)
        report = OperatingEnvelopeAnalyzer().analyze(
            sweep_reports=sweep_reports,
            endurance_reports=endurance_reports,
            sweep_sources=tuple(str(path) for path in sweep_paths),
            endurance_sources=tuple(str(path) for path in endurance_paths),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"operating-envelope analysis failed: {exc}", file=sys.stderr)
        return 2


def _load_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: report must be a JSON object")
    return cast(Mapping[str, Any], payload)


if __name__ == "__main__":
    raise SystemExit(main())
