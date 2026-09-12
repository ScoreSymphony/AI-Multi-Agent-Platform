"""CLI for validating measured inference-backend evidence and decision readiness."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .inference_backend_evaluation import assess_inference_backend_evaluation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-inference-backend-evaluation")
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="return exit code 3 when evidence validates but is not decision-ready",
    )
    return parser


def _reject_non_finite_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON numeric constant is not permitted: {value}")


def _load_object(path: Path, *, label: str) -> Mapping[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_non_finite_json_constant,
    )
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must contain a JSON object")
    return cast(Mapping[str, Any], payload)


def _require_distinct_output(*, output: Path, inputs: tuple[Path, ...]) -> None:
    output_resolved = output.resolve()
    for evidence_path in inputs:
        if output_resolved == evidence_path.resolve():
            raise ValueError("output must not alias campaign or report evidence")
        if output.exists() and evidence_path.exists() and output.samefile(evidence_path):
            raise ValueError("output must not alias campaign or report evidence")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    campaign_path = cast(Path, args.campaign)
    report_paths = tuple(cast(list[Path], args.report))
    output = cast(Path, args.output)
    require_ready = cast(bool, args.require_ready)

    try:
        _require_distinct_output(
            output=output,
            inputs=(campaign_path, *report_paths),
        )
        campaign = _load_object(campaign_path, label="campaign")
        reports = tuple(
            _load_object(report_path, label=f"report {report_path}") for report_path in report_paths
        )
        readiness = assess_inference_backend_evaluation(
            campaign=campaign,
            reports=reports,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(readiness.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if require_ready and not readiness.ready_for_decision:
            return 3
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"inference backend evidence validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
