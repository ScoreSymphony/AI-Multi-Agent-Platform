"""API-first CLI surface for the unified Marketplace."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

Confirmation = Callable[[argparse.Namespace, str, str], None]


def add_marketplace_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    marketplace = areas.add_parser(
        "marketplace",
        help="search, inspect and manage unified Marketplace components",
    )
    marketplace.set_defaults(area="marketplace")
    commands = marketplace.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="search across Marketplace component kinds")
    search.add_argument("query", nargs="?")
    _add_search_arguments(search)

    list_command = commands.add_parser("list", help="list Marketplace components")
    _add_search_arguments(list_command)

    show = commands.add_parser("show", help="show one Marketplace component")
    _add_item_arguments(show, version_required=False)

    status = commands.add_parser(
        "status",
        help="show install/update state and owner status for one component",
    )
    _add_item_arguments(status, version_required=False)

    preview = commands.add_parser(
        "preview",
        help="run the canonical validation path without mutating owner state",
    )
    _add_item_arguments(preview, version_required=True)
    preview.add_argument("--idempotency-key")

    install = commands.add_parser(
        "install",
        help="install through the canonical Marketplace owner route",
    )
    _add_item_arguments(install, version_required=True)
    install.add_argument("--idempotency-key")

    update = commands.add_parser(
        "update",
        help="update an installed component through its canonical owner route",
    )
    _add_item_arguments(update, version_required=True)
    update.add_argument("--idempotency-key")

    uninstall = commands.add_parser(
        "uninstall",
        help="uninstall through the canonical owner handler when supported",
    )
    uninstall.add_argument("item_id")
    uninstall.add_argument("--idempotency-key")


def execute_marketplace(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    if args.command in {"search", "list"}:
        query_text = getattr(args, "query", None)
        return client.get(
            "/registry-items",
            query=_search_query(args, query_text=query_text),
        )

    item_id = str(args.item_id)
    version = getattr(args, "version", None)
    if args.command in {"show", "status"}:
        resource_id = item_id if version is None else f"{item_id}@{version}"
        source = getattr(args, "source", None)
        if source:
            resource_id = f"{source}::{resource_id}"
        return client.get(f"/registry-items/{quote(resource_id, safe='')}")

    if args.command == "preview":
        action = "preview"
        version = str(args.version)
    elif args.command == "install":
        action = "install"
        version = str(args.version)
        confirm(args, "install marketplace item", f"{item_id}@{version}")
    elif args.command == "update":
        action = "update"
        version = str(args.version)
        confirm(args, "update marketplace item", f"{item_id}@{version}")
    elif args.command == "uninstall":
        action = "uninstall"
        version = None
        confirm(args, "uninstall marketplace item", item_id)
    else:
        raise ProfileError(f"unsupported marketplace command: {args.command}")

    body: dict[str, JsonValue] = {"resource_ref": item_id}
    if version is not None:
        body["version"] = version
    source = getattr(args, "source", None)
    if source is not None:
        body["source_registry"] = str(source)
    return client.post(
        f"/commands/marketplace.{action}",
        body=body,
        idempotency_key=args.idempotency_key,
    )


def _add_item_arguments(
    parser: argparse.ArgumentParser,
    *,
    version_required: bool,
) -> None:
    parser.add_argument("item_id")
    parser.add_argument("--source")
    if version_required:
        parser.add_argument("version")
    else:
        parser.add_argument("version", nargs="?")


def _add_search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument(
        "--sort",
        default="id",
        choices=[
            "id",
            "name",
            "publisher",
            "source_registry",
            "source",
            "version",
            "released_at",
            "release_date",
            "kind",
            "item_type",
        ],
    )
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--kind", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--publisher", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--license", dest="licenses", action="append", default=[])
    parser.add_argument("--trust", action="append", default=[])
    parser.add_argument("--installed", choices=["true", "false"])
    parser.add_argument("--update-available", choices=["true", "false"])
    parser.add_argument("--deprecated", choices=["true", "false"])
    parser.add_argument("--yanked", choices=["true", "false"])
    parser.add_argument("--compatible", choices=["true", "false"])
    parser.add_argument("--platform-version")
    parser.add_argument("--required-capability", action="append", default=[])
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")


def _search_query(
    args: argparse.Namespace,
    *,
    query_text: str | None,
) -> dict[str, str]:
    query = {
        "limit": str(args.limit),
        "sort": str(args.sort),
        "direction": str(args.direction),
    }
    if args.cursor:
        query["cursor"] = str(args.cursor)
    if query_text:
        query["q"] = query_text
    if args.fields:
        query["fields"] = str(args.fields)

    _add_csv_filter(query, "kind", args.kind)
    _add_csv_filter(query, "tag", args.tag)
    _add_csv_filter(query, "category", args.category)
    _add_csv_filter(query, "publisher", args.publisher)
    _add_csv_filter(query, "source", args.source)
    _add_csv_filter(query, "license", args.licenses)
    _add_csv_filter(query, "trust", args.trust)
    _add_csv_filter(query, "required_capability", args.required_capability)
    for field, attribute in (
        ("installed", "installed"),
        ("update_available", "update_available"),
        ("deprecated", "deprecated"),
        ("yanked", "yanked"),
        ("compatible", "compatible"),
        ("platform_version", "platform_version"),
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            query[f"filter[{field}]"] = str(value)

    for raw_filter in args.filter:
        field, separator, value = str(raw_filter).partition("=")
        if not separator or not field.strip() or not value.strip():
            raise ProfileError("--filter must use FIELD=VALUE")
        key = f"filter[{field.strip()}]"
        if key in query:
            raise ProfileError(f"duplicate Marketplace filter: {field.strip()}")
        query[key] = value.strip()
    return query


def _add_csv_filter(
    query: dict[str, str],
    field: str,
    values: list[str],
) -> None:
    flattened = [part.strip() for value in values for part in str(value).split(",") if part.strip()]
    if flattened:
        query[f"filter[{field}]"] = ",".join(dict.fromkeys(flattened))
