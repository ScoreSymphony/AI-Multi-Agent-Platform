"""CLI adapter for optional Registry/Marketplace operations."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

Confirmation = Callable[[argparse.Namespace, str, str], None]


def add_registry_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    registry = areas.add_parser("registry", help="discover and activate Registry assets")
    registry.set_defaults(area="registry")
    commands = registry.add_subparsers(dest="command", required=True)

    list_command = commands.add_parser("list", help="list available Registry items")
    _add_pagination_arguments(list_command)

    show = commands.add_parser("show", help="show one exact Registry item version")
    _add_item_version_arguments(show)

    preview = commands.add_parser("preview", help="validate one Registry item before mutation")
    _add_item_version_arguments(preview)
    preview.add_argument("--idempotency-key")

    activate = commands.add_parser(
        "activate",
        help="activate one validated Registry item through its canonical owner",
    )
    _add_item_version_arguments(activate)
    activate.add_argument("--idempotency-key")

    pin = commands.add_parser("pin", help="pin the currently installed Registry item version")
    _add_item_version_arguments(pin)
    pin.add_argument("--idempotency-key")

    unpin = commands.add_parser("unpin", help="remove the version pin from an installed item")
    unpin.add_argument("item_id")
    unpin.add_argument("--idempotency-key")


def execute_registry(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    if args.command == "list":
        return client.get("/registry-items", query=_page_query(args))

    item_id = str(args.item_id)
    if args.command == "show":
        show_version = str(args.version)
        resource_id = quote(f"{item_id}@{show_version}", safe="")
        return client.get(f"/registry-items/{resource_id}")

    version: str | None
    if args.command == "activate":
        version = str(args.version)
        confirm(args, "activate registry item", f"{item_id}@{version}")
    elif args.command in {"preview", "pin"}:
        version = str(args.version)
    elif args.command == "unpin":
        version = None
    else:
        raise ProfileError(f"unsupported registry command: {args.command}")

    body: dict[str, JsonValue] = {"resource_ref": item_id}
    if version is not None:
        body["version"] = version
    return client.post(
        f"/commands/registry.{args.command}",
        body=body,
        idempotency_key=args.idempotency_key,
    )


def _add_item_version_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("item_id")
    parser.add_argument("version")


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
