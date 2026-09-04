"""API-first CLI adapter for canonical evaluation and regression resources."""

from __future__ import annotations

import argparse
import json
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError


def add_evaluation_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the canonical Evaluation CLI surface."""

    evaluation = areas.add_parser(
        "eval",
        help="run and inspect canonical evaluations and regression comparisons",
    )
    evaluation.set_defaults(area="evaluation")
    commands = evaluation.add_subparsers(dest="command", required=True)

    suite = commands.add_parser("suite", help="inspect configured versioned evaluation suites")
    suite_commands = suite.add_subparsers(dest="suite_command", required=True)
    suite_list = suite_commands.add_parser("list", help="list configured evaluation suites")
    _add_pagination_arguments(suite_list)
    suite_show = suite_commands.add_parser("show", help="show one exact versioned suite")
    suite_show.add_argument("suite_ref", help="exact suite reference in <id>@<version> form")

    run = commands.add_parser("run", help="execute one configured evaluation suite")
    run.add_argument("suite_ref", help="exact suite reference in <id>@<version> form")
    run.add_argument(
        "--snapshot-json",
        required=True,
        help=(
            "immutable ConfigurationSnapshot JSON object containing at least platform_version; "
            "references/environment use the canonical Evaluation API schema"
        ),
    )
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--seed", type=int)
    run.add_argument("--baseline-run-id")
    run.add_argument("--regression-policy-ref")
    run.add_argument(
        "--aggregation-policy-ref",
        help="exact versioned aggregation policy required for repeated baseline comparison",
    )
    run.add_argument("--idempotency-key")

    result = commands.add_parser(
        "result",
        help="inspect durable evaluation-run detail and evaluator results",
    )
    result_commands = result.add_subparsers(dest="result_command", required=True)
    result_show = result_commands.add_parser(
        "show",
        help="show one durable evaluation run with raw results, aggregates and comparison",
    )
    result_show.add_argument("run_id")

    compare = commands.add_parser(
        "compare",
        help="persist a regression comparison for a completed current run",
    )
    compare.add_argument("current_run_id")
    compare.add_argument("--baseline-run-id", required=True)
    compare.add_argument("--regression-policy-ref", required=True)
    compare.add_argument(
        "--aggregation-policy-ref",
        help="exact versioned aggregation policy required when either run is repeated",
    )
    compare.add_argument("--idempotency-key")


def execute_evaluation(
    args: argparse.Namespace,
    client: ControlPlaneClient,
) -> ClientResponse:
    """Execute Evaluation commands only through the canonical Control Plane API."""

    if args.command == "suite":
        if args.suite_command == "list":
            return client.get("/evaluation-suites", query=_page_query(args))
        if args.suite_command == "show":
            return client.get(f"/evaluation-suites/{_segment(args.suite_ref)}")
        raise ProfileError(f"unsupported evaluation suite command: {args.suite_command}")

    if args.command == "run":
        if args.repetitions <= 0:
            raise ProfileError("--repetitions must be greater than zero")
        body: dict[str, JsonValue] = {
            "resource_ref": str(args.suite_ref),
            "snapshot": parse_snapshot(args.snapshot_json),
            "repetitions": args.repetitions,
        }
        if args.seed is not None:
            body["seed"] = args.seed
        if args.baseline_run_id is not None:
            body["baseline_run_id"] = str(args.baseline_run_id)
        if args.regression_policy_ref is not None:
            body["regression_policy_ref"] = str(args.regression_policy_ref)
        if args.aggregation_policy_ref is not None:
            body["aggregation_policy_ref"] = str(args.aggregation_policy_ref)
        return client.post(
            "/commands/evaluation.run",
            body=body,
            idempotency_key=args.idempotency_key,
        )

    if args.command == "result":
        if args.result_command == "show":
            return client.get(f"/evaluation-runs/{_segment(args.run_id)}")
        raise ProfileError(f"unsupported evaluation result command: {args.result_command}")

    if args.command == "compare":
        body: dict[str, JsonValue] = {
            "resource_ref": str(args.current_run_id),
            "baseline_run_id": str(args.baseline_run_id),
            "regression_policy_ref": str(args.regression_policy_ref),
        }
        if args.aggregation_policy_ref is not None:
            body["aggregation_policy_ref"] = str(args.aggregation_policy_ref)
        return client.post(
            "/commands/evaluation.compare",
            body=body,
            idempotency_key=args.idempotency_key,
        )

    raise ProfileError(f"unsupported evaluation command: {args.command}")


def parse_snapshot(raw: str) -> dict[str, JsonValue]:
    """Parse an explicit immutable snapshot without duplicating API domain validation."""

    try:
        value: JsonValue = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileError("--snapshot-json must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ProfileError("--snapshot-json must be a JSON object")
    platform_version = value.get("platform_version")
    if not isinstance(platform_version, str) or not platform_version.strip():
        raise ProfileError("--snapshot-json must contain non-blank platform_version")
    return value


def _add_pagination_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    query = {"limit": str(args.limit), "sort": str(args.sort), "direction": str(args.direction)}
    if args.cursor:
        query["cursor"] = str(args.cursor)
    if args.q:
        query["q"] = str(args.q)
    if args.fields:
        query["fields"] = str(args.fields)
    for raw_filter in args.filter:
        field, separator, value = str(raw_filter).partition("=")
        if not separator or not field or not value:
            raise ProfileError("--filter must use FIELD=VALUE")
        query[f"filter[{field}]"] = value
    return query


def _segment(value: str) -> str:
    return quote(value, safe="")


__all__ = ["add_evaluation_parser", "execute_evaluation", "parse_snapshot"]
