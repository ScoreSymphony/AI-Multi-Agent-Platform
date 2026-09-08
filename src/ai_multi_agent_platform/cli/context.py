"""Read-only CLI inspection for canonical Context Bundle evidence."""

from __future__ import annotations

import argparse

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient

CONTEXT_BUNDLE_COLLECTION = "context-bundles"
CONTEXT_RUN_BINDING_COLLECTION = "context-run-bindings"


def add_context_parser(areas: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    context = areas.add_parser(
        "context",
        help="inspect canonical Context Bundles and Run bindings",
    )
    context.set_defaults(area="context")
    commands = context.add_subparsers(dest="command", required=True)

    for_run = commands.add_parser(
        "for-run",
        help="show all canonical Context evidence bound to one platform Run",
    )
    for_run.add_argument("run_id")

    bundle = commands.add_parser("bundle", help="show one canonical Context Bundle")
    bundle.add_argument("context_bundle_id")

    binding = commands.add_parser(
        "binding",
        help="show the Context binding for one canonical AgentRun",
    )
    binding.add_argument("agent_run_id")

    bundles = commands.add_parser("list-bundles", help="list canonical Context Bundles")
    _add_page_arguments(bundles)

    bindings = commands.add_parser("list-bindings", help="list canonical Context Run bindings")
    bindings.add_argument("--run-id")
    _add_page_arguments(bindings)


def execute_context(args: argparse.Namespace, client: ControlPlaneClient) -> ClientResponse:
    if args.command == "bundle":
        return client.get(
            f"/{CONTEXT_BUNDLE_COLLECTION}/{_segment(args.context_bundle_id)}"
        )
    if args.command == "binding":
        return client.get(
            f"/{CONTEXT_RUN_BINDING_COLLECTION}/{_segment(args.agent_run_id)}"
        )
    if args.command == "list-bundles":
        return client.get(
            f"/{CONTEXT_BUNDLE_COLLECTION}",
            query=_page_query(args),
        )
    if args.command == "list-bindings":
        query = _page_query(args)
        if args.run_id:
            query["filter[run_id]"] = args.run_id
        return client.get(f"/{CONTEXT_RUN_BINDING_COLLECTION}", query=query)
    if args.command == "for-run":
        return _context_for_run(args.run_id, client)
    raise ValueError(f"unsupported context command: {args.command}")


def _context_for_run(run_id: str, client: ControlPlaneClient) -> ClientResponse:
    bindings_response = client.get(
        f"/{CONTEXT_RUN_BINDING_COLLECTION}",
        query={
            "limit": "100",
            "sort": "id",
            "direction": "asc",
            "filter[run_id]": run_id,
        },
    )
    bindings = _page_items(bindings_response.body)
    bundles: list[JsonValue] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        bundle_id = binding.get("context_bundle_id")
        if not isinstance(bundle_id, str) or not bundle_id.strip():
            continue
        bundles.append(
            client.get(f"/{CONTEXT_BUNDLE_COLLECTION}/{_segment(bundle_id)}").body
        )
    return ClientResponse(
        status=bindings_response.status,
        body={
            "run_id": run_id,
            "bindings": bindings,
            "bundles": bundles,
            "binding_count": len(bindings),
            "bundle_count": len(bundles),
        },
        request_id=bindings_response.request_id,
        correlation_id=bindings_response.correlation_id,
        api_version=bindings_response.api_version,
    )


def _page_items(body: JsonValue) -> list[JsonValue]:
    if not isinstance(body, dict):
        raise ValueError("Context collection response must be a JSON object")
    items = body.get("items")
    if not isinstance(items, list):
        raise ValueError("Context collection response must contain an items array")
    return items


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    query = {
        "limit": str(args.limit),
        "sort": args.sort,
        "direction": args.direction,
    }
    if args.cursor:
        query["cursor"] = args.cursor
    if args.q:
        query["q"] = args.q
    return query


def _add_page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")


def _segment(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


__all__ = ["add_context_parser", "execute_context"]
