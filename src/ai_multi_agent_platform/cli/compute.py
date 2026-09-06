"""CLI surface for canonical Node/Worker resources from issue #14."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient, TransportError
from .profiles import ProfileError

Confirmation = Callable[[argparse.Namespace, str, str], None]


def add_compute_parsers(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register Node, Worker and WorkerJob commands over the Control Plane only."""

    node = areas.add_parser("node", help="inspect and administer canonical compute nodes")
    node.set_defaults(area="node")
    node_commands = node.add_subparsers(dest="command", required=True)
    _add_list_parser(node_commands, "list", "list canonical nodes")
    node_show = node_commands.add_parser("show", help="show one canonical node")
    node_show.add_argument("node_id")
    for command in ("drain", "undrain", "maintenance-enable", "maintenance-disable"):
        action = node_commands.add_parser(command, help=f"{command} canonical node")
        action.add_argument("node_id")
        action.add_argument("--idempotency-key")

    worker = areas.add_parser("worker", help="inspect and administer canonical workers")
    worker.set_defaults(area="worker")
    worker_commands = worker.add_subparsers(dest="command", required=True)
    _add_list_parser(worker_commands, "list", "list canonical workers")
    worker_show = worker_commands.add_parser("show", help="show one canonical worker")
    worker_show.add_argument("worker_id")
    for command in ("drain", "undrain"):
        action = worker_commands.add_parser(command, help=f"{command} canonical worker")
        action.add_argument("worker_id")
        action.add_argument("--idempotency-key")

    provision = worker_commands.add_parser(
        "provision",
        help="provision the profile reporter Worker credential and minimal #15 policy",
    )
    provision.add_argument("worker_id")
    provision.add_argument("--purpose")
    provision.add_argument("--idempotency-key")

    rotate = worker_commands.add_parser(
        "rotate-credential",
        help="rotate one profile reporter Worker credential",
    )
    rotate.add_argument("worker_id")
    rotate.add_argument("--credential-id", required=True)
    rotate.add_argument("--purpose")
    rotate.add_argument("--idempotency-key")

    worker_job = areas.add_parser("worker-job", help="inspect canonical WorkerJob dispatch state")
    worker_job.set_defaults(area="worker-job")
    worker_job_commands = worker_job.add_subparsers(dest="command", required=True)
    _add_list_parser(worker_job_commands, "list", "list canonical worker jobs")
    worker_job_show = worker_job_commands.add_parser("show", help="show one canonical worker job")
    worker_job_show.add_argument("worker_job_id")


def execute_compute(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    """Execute canonical compute inspection/admin commands without backend shortcuts."""

    if args.area == "node":
        return _execute_node(args, client, confirm)
    if args.area == "worker":
        return _execute_worker(args, client, confirm)
    if args.area == "worker-job":
        return _execute_worker_job(args, client)
    raise ProfileError(f"unsupported compute command area: {args.area}")


def doctor_compute(client: ControlPlaneClient) -> tuple[str, list[JsonValue]]:
    """Inspect optional canonical compute resources for `platform doctor`."""

    responses: dict[str, ClientResponse] = {}
    for kind, path in (("node", "/nodes"), ("worker", "/workers")):
        try:
            responses[kind] = client.get(path, query={"limit": "200"}, raise_for_status=False)
        except TransportError as exc:
            return "degraded", [
                {
                    "name": "compute_health",
                    "status": "degraded",
                    "message": str(exc),
                }
            ]

    if all(response.status == 404 for response in responses.values()):
        return "healthy", []

    overall = "healthy"
    checks: list[JsonValue] = []
    for kind, response in responses.items():
        if response.status == 404:
            overall = "degraded"
            checks.append(
                {
                    "name": f"{kind}_health",
                    "status": "degraded",
                    "message": f"canonical {kind} collection missing from partial compute surface",
                }
            )
            continue
        if response.status >= 400:
            overall = "degraded"
            checks.append(
                {
                    "name": f"{kind}_health",
                    "status": "degraded",
                    "http_status": response.status,
                }
            )
            continue

        items = _page_items(response.body)
        if items is None:
            overall = "degraded"
            checks.append(
                {
                    "name": f"{kind}_health",
                    "status": "degraded",
                    "message": f"canonical {kind} collection returned an invalid page",
                }
            )
            continue
        if not items:
            overall = "degraded"
            checks.append(
                {
                    "name": f"{kind}_health",
                    "status": "degraded",
                    "message": f"no canonical {kind} resources are registered",
                }
            )
            continue

        for item in items:
            item_status = _compute_item_status(kind, item)
            if item_status == "degraded":
                overall = "degraded"
            checks.append(
                {
                    "name": f"{kind}_health",
                    "status": item_status,
                    "resource_id": item.get("id"),
                    "resource_status": item.get("status"),
                    "draining": item.get("draining", False),
                    "maintenance": item.get("maintenance", False) if kind == "node" else False,
                }
            )
    return overall, checks


def _execute_node(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    if args.command == "list":
        return client.get("/nodes", query=_page_query(args))
    if args.command == "show":
        return client.get(f"/nodes/{_segment(args.node_id)}")
    if args.command in {"drain", "undrain", "maintenance-enable", "maintenance-disable"}:
        node_id = str(args.node_id)
        confirm(args, f"{args.command} node", node_id)
        return client.post(
            f"/commands/node.{args.command}",
            body={"resource_ref": node_id},
            idempotency_key=args.idempotency_key,
        )
    raise ProfileError(f"unsupported node command: {args.command}")


def _execute_worker(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    if args.command == "list":
        return client.get("/workers", query=_page_query(args))
    if args.command == "show":
        return client.get(f"/workers/{_segment(args.worker_id)}")
    if args.command in {"drain", "undrain"}:
        worker_id = str(args.worker_id)
        confirm(args, f"{args.command} worker", worker_id)
        return client.post(
            f"/commands/worker.{args.command}",
            body={"resource_ref": worker_id},
            idempotency_key=args.idempotency_key,
        )
    if args.command == "provision":
        worker_id = str(args.worker_id)
        confirm(args, "provision Worker credential", worker_id)
        body: dict[str, JsonValue] = {"resource_ref": worker_id}
        if args.purpose:
            body["purpose"] = str(args.purpose)
        return client.post(
            "/commands/worker.provision",
            body=body,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "rotate-credential":
        worker_id = str(args.worker_id)
        confirm(args, "rotate Worker credential", worker_id)
        body = {
            "resource_ref": worker_id,
            "credential_id": str(args.credential_id),
        }
        if args.purpose:
            body["purpose"] = str(args.purpose)
        return client.post(
            "/commands/worker.rotate-credential",
            body=body,
            idempotency_key=args.idempotency_key,
        )
    raise ProfileError(f"unsupported worker command: {args.command}")


def _execute_worker_job(args: argparse.Namespace, client: ControlPlaneClient) -> ClientResponse:
    if args.command == "list":
        return client.get("/worker-jobs", query=_page_query(args))
    if args.command == "show":
        return client.get(f"/worker-jobs/{_segment(args.worker_job_id)}")
    raise ProfileError(f"unsupported worker-job command: {args.command}")


def _page_items(body: JsonValue) -> list[dict[str, JsonValue]] | None:
    if not isinstance(body, dict):
        return None
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
        return None
    return [item for item in raw_items if isinstance(item, dict)]


def _compute_item_status(kind: str, item: dict[str, JsonValue]) -> str:
    status = item.get("status")
    draining = item.get("draining") is True
    if kind == "node":
        healthy = status == "online" and not draining and item.get("maintenance") is not True
    else:
        healthy = status == "healthy" and not draining
    return "healthy" if healthy else "degraded"


def _add_list_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")
    return parser


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
