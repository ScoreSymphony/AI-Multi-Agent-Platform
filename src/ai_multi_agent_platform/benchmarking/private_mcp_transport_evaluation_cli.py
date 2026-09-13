"""CLI for validating and assessing retained private remote MCP evaluation evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .private_mcp_transport_evaluation import assess_private_mcp_transport_evaluation
from .private_mcp_transport_evidence import verify_private_mcp_transport_evidence_files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-private-mcp-evaluation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="validate the report contract and verify every retained evidence file/hash",
    )
    _add_common_arguments(validate)

    assess = subparsers.add_parser(
        "assess",
        help="verify retained evidence and compute decision/adoption/completion readiness",
    )
    _add_common_arguments(assess)
    assess.add_argument("--minimum-latency-samples", type=int, default=5)
    assess.add_argument("--require-decision-ready", action="store_true")
    assess.add_argument("--require-adoption-eligible", action="store_true")
    assess.add_argument("--require-definition-of-done", action="store_true")
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        required=True,
        help="explicit root against which raw_evidence paths are resolved",
    )
    parser.add_argument("--output", type=Path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = _load_report(cast(Path, args.report))
        verification = verify_private_mcp_transport_evidence_files(
            report,
            evidence_root=cast(Path, args.evidence_root),
        )

        if args.command == "validate":
            _emit(
                {
                    "valid": True,
                    "evidence": verification.to_dict(),
                },
                cast(Path | None, args.output),
            )
            return 0

        readiness = assess_private_mcp_transport_evaluation(
            report,
            minimum_latency_samples=cast(int, args.minimum_latency_samples),
        )
        payload = {
            "valid": True,
            "evidence": verification.to_dict(),
            "readiness": readiness.to_dict(),
        }
        _emit(payload, cast(Path | None, args.output))

        if cast(bool, args.require_decision_ready) and not readiness.decision_ready:
            return 3
        if cast(bool, args.require_adoption_eligible) and not readiness.adoption_eligible:
            return 3
        if cast(bool, args.require_definition_of_done) and not readiness.definition_of_done:
            return 3
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"private MCP evaluation failed: {exc}", file=sys.stderr)
        return 2


def _load_report(path: Path) -> Mapping[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("private MCP evaluation report must be a JSON object")
    return cast(dict[str, Any], payload)


def _emit(payload: Mapping[str, Any], output: Path | None) -> None:
    encoded = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(encoded)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
