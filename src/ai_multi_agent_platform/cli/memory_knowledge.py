"""API-first CLI adapter for canonical Memory and Knowledge lifecycle resources."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import cast
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

Confirmation = Callable[[argparse.Namespace, str, str], None]

_MEMORY_SCOPES = (
    "short_term",
    "task",
    "agent",
    "workspace",
    "user",
    "historical",
    "organization",
)
_MEMORY_ORIGINS = ("user-authored", "agent-derived", "imported")
_KNOWLEDGE_MODES = ("keyword", "semantic", "hybrid")


def add_memory_knowledge_parsers(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register canonical Memory and Knowledge CLI surfaces."""

    memory = areas.add_parser("memory", help="inspect and manage canonical Memory content")
    memory.set_defaults(area="memory")
    memory_commands = memory.add_subparsers(dest="command", required=True)

    memory_list = memory_commands.add_parser("list", help="list Memory in one canonical scope")
    _add_page_arguments(memory_list)
    memory_list.add_argument("--scope", choices=_MEMORY_SCOPES)
    memory_list.add_argument("--scope-id")
    memory_list.add_argument("--project-id")
    memory_list.add_argument("--owner-ref")
    memory_list.add_argument("--include-expired", action="store_true")
    memory_list.add_argument("--include-superseded", action="store_true")

    memory_show = memory_commands.add_parser("show", help="show one canonical Memory entry")
    memory_show.add_argument("memory_id")

    memory_create = memory_commands.add_parser("create", help="create an explicit Memory entry")
    memory_create.add_argument("scope_id")
    memory_create.add_argument("--scope", required=True, choices=_MEMORY_SCOPES)
    memory_create.add_argument("--origin", required=True, choices=_MEMORY_ORIGINS)
    memory_create.add_argument("--value-json", required=True)
    memory_create.add_argument("--project-id")
    memory_create.add_argument("--retention")
    memory_create.add_argument("--expires-at")
    memory_create.add_argument("--classification")
    memory_create.add_argument("--metadata-json")
    memory_create.add_argument("--provenance-json")
    memory_create.add_argument("--idempotency-key")

    promote = memory_commands.add_parser(
        "promote",
        help="promote canonical short-term Memory into a durable scope",
    )
    promote.add_argument("memory_id")
    promote.add_argument("--scope", required=True, choices=_MEMORY_SCOPES)
    promote.add_argument("--scope-id", required=True)
    promote.add_argument("--project-id")
    promote.add_argument("--retention")
    promote.add_argument("--expires-at")
    promote.add_argument("--idempotency-key")

    update = memory_commands.add_parser("update", help="supersede a canonical Memory entry")
    update.add_argument("memory_id")
    update.add_argument("--value-json")
    update.add_argument("--retention")
    update.add_argument("--expires-at")
    update.add_argument("--classification")
    update.add_argument("--metadata-json")
    update.add_argument("--idempotency-key")

    expire = memory_commands.add_parser("expire", help="expire one due Memory entry")
    expire.add_argument("memory_id")
    expire.add_argument("--scope", required=True, choices=_MEMORY_SCOPES)
    expire.add_argument("--scope-id", required=True)
    expire.add_argument("--project-id")
    expire.add_argument("--idempotency-key")

    delete = memory_commands.add_parser("delete", help="delete one visible Memory entry")
    delete.add_argument("memory_id")
    delete.add_argument("--idempotency-key")

    knowledge = areas.add_parser(
        "knowledge",
        help="inspect, query and manage canonical Knowledge sources",
    )
    knowledge.set_defaults(area="knowledge")
    knowledge_commands = knowledge.add_subparsers(dest="command", required=True)

    knowledge_list = knowledge_commands.add_parser("list", help="list canonical Knowledge sources")
    _add_page_arguments(knowledge_list)
    knowledge_list.add_argument("--project-id")

    knowledge_show = knowledge_commands.add_parser("show", help="show one Knowledge source")
    knowledge_show.add_argument("source_id")

    search = knowledge_commands.add_parser("search", help="query source-backed Knowledge")
    search.add_argument("query")
    _add_page_arguments(search, include_search=False)
    search.add_argument("--project-id")
    search.add_argument("--source-id")
    search.add_argument("--mode", choices=_KNOWLEDGE_MODES, default="keyword")

    register = knowledge_commands.add_parser("register", help="register a Knowledge source")
    register.add_argument(
        "target_ref",
        help="Project ID for project-scoped sources or authenticated principal ref otherwise",
    )
    register.add_argument("--title", required=True)
    register.add_argument("--project-id")
    register.add_argument("--revision")
    register.add_argument("--metadata-json")
    register.add_argument("--idempotency-key")

    knowledge_update = knowledge_commands.add_parser(
        "update",
        help="update canonical Knowledge source metadata",
    )
    knowledge_update.add_argument("source_id")
    knowledge_update.add_argument("--title")
    knowledge_update.add_argument("--metadata-json")
    knowledge_update.add_argument("--idempotency-key")

    ingest = knowledge_commands.add_parser("ingest", help="ingest source-backed Knowledge content")
    ingest.add_argument("source_id")
    ingest.add_argument("--content", required=True)
    ingest.add_argument("--location", required=True)
    ingest.add_argument("--idempotency-key")

    reindex = knowledge_commands.add_parser("reindex", help="re-index an explicit source revision")
    reindex.add_argument("source_id")
    reindex.add_argument("--revision", required=True)
    reindex.add_argument("--content", required=True)
    reindex.add_argument("--location", required=True)
    reindex.add_argument("--idempotency-key")

    for command in ("detach", "delete"):
        action = knowledge_commands.add_parser(command, help=f"{command} a Knowledge source")
        action.add_argument("source_id")
        action.add_argument("--idempotency-key")


def execute_memory(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    """Execute Memory operations only through canonical Control Plane resources/commands."""

    if args.command == "list":
        query = _page_query(args)
        _set_filter(query, "scope", args.scope)
        _set_filter(query, "scope_id", args.scope_id)
        _set_filter(query, "project_id", args.project_id)
        _set_filter(query, "owner_ref", args.owner_ref)
        if args.include_expired:
            _set_filter(query, "include_expired", "true")
        if args.include_superseded:
            _set_filter(query, "include_superseded", "true")
        return client.get("/memory", query=query)
    if args.command == "show":
        return client.get(f"/memory/{_segment(args.memory_id)}")

    if args.command == "create":
        body: dict[str, JsonValue] = {
            "resource_ref": str(args.scope_id),
            "scope": str(args.scope),
            "scope_id": str(args.scope_id),
            "origin": str(args.origin),
            "value": _parse_json(args.value_json, "--value-json"),
        }
        _copy_optional(body, "project_id", args.project_id)
        _copy_optional(body, "retention", args.retention)
        _copy_optional(body, "expires_at", args.expires_at)
        _copy_optional(body, "classification", args.classification)
        if args.metadata_json is not None:
            body["metadata"] = _parse_object(args.metadata_json, "--metadata-json")
        if args.provenance_json is not None:
            body["provenance"] = _parse_array(args.provenance_json, "--provenance-json")
        return _post_command(client, "memory.create", body, args.idempotency_key)

    memory_id = str(args.memory_id)
    if args.command == "promote":
        body = {
            "resource_ref": memory_id,
            "scope": str(args.scope),
            "scope_id": str(args.scope_id),
        }
        _copy_optional(body, "project_id", args.project_id)
        _copy_optional(body, "retention", args.retention)
        _copy_optional(body, "expires_at", args.expires_at)
        return _post_command(client, "memory.promote", body, args.idempotency_key)
    if args.command == "update":
        body = {"resource_ref": memory_id}
        if args.value_json is not None:
            body["value"] = _parse_json(args.value_json, "--value-json")
        _copy_optional(body, "retention", args.retention)
        _copy_optional(body, "expires_at", args.expires_at)
        _copy_optional(body, "classification", args.classification)
        if args.metadata_json is not None:
            body["metadata"] = _parse_object(args.metadata_json, "--metadata-json")
        if len(body) == 1:
            raise ProfileError("memory update requires at least one changed field")
        return _post_command(client, "memory.update", body, args.idempotency_key)
    if args.command == "expire":
        confirm(args, "expire Memory", memory_id)
        body = {
            "resource_ref": memory_id,
            "scope": str(args.scope),
            "scope_id": str(args.scope_id),
        }
        _copy_optional(body, "project_id", args.project_id)
        return _post_command(client, "memory.expire", body, args.idempotency_key)
    if args.command == "delete":
        confirm(args, "delete Memory", memory_id)
        return _post_command(
            client,
            "memory.delete",
            {"resource_ref": memory_id},
            args.idempotency_key,
        )
    raise ProfileError(f"unsupported memory command: {args.command}")


def execute_knowledge(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    """Execute Knowledge operations only through canonical Control Plane resources/commands."""

    if args.command == "list":
        query = _page_query(args)
        _set_filter(query, "project_id", args.project_id)
        return client.get("/knowledge", query=query)
    if args.command == "show":
        return client.get(f"/knowledge/{_segment(args.source_id)}")
    if args.command == "search":
        query = _page_query(args, search=str(args.query))
        _set_filter(query, "project_id", args.project_id)
        _set_filter(query, "source_id", args.source_id)
        _set_filter(query, "mode", args.mode)
        return client.get("/knowledge-results", query=query)

    if args.command == "register":
        body: dict[str, JsonValue] = {
            "resource_ref": str(args.target_ref),
            "title": str(args.title),
        }
        _copy_optional(body, "project_id", args.project_id)
        _copy_optional(body, "revision", args.revision)
        if args.metadata_json is not None:
            body["metadata"] = _parse_object(args.metadata_json, "--metadata-json")
        return _post_command(client, "knowledge.register", body, args.idempotency_key)

    source_id = str(args.source_id)
    if args.command == "update":
        body = {"resource_ref": source_id}
        _copy_optional(body, "title", args.title)
        if args.metadata_json is not None:
            body["metadata"] = _parse_object(args.metadata_json, "--metadata-json")
        if len(body) == 1:
            raise ProfileError("knowledge update requires --title and/or --metadata-json")
        return _post_command(client, "knowledge.update", body, args.idempotency_key)
    if args.command == "ingest":
        return _post_command(
            client,
            "knowledge.ingest",
            {
                "resource_ref": source_id,
                "content": str(args.content),
                "location": str(args.location),
            },
            args.idempotency_key,
        )
    if args.command == "reindex":
        return _post_command(
            client,
            "knowledge.reindex",
            {
                "resource_ref": source_id,
                "revision": str(args.revision),
                "content": str(args.content),
                "location": str(args.location),
            },
            args.idempotency_key,
        )
    if args.command in {"detach", "delete"}:
        confirm(args, f"{args.command} Knowledge source", source_id)
        return _post_command(
            client,
            f"knowledge.{args.command}",
            {"resource_ref": source_id},
            args.idempotency_key,
        )
    raise ProfileError(f"unsupported knowledge command: {args.command}")


def _post_command(
    client: ControlPlaneClient,
    command: str,
    body: dict[str, JsonValue],
    idempotency_key: str | None,
) -> ClientResponse:
    return client.post(f"/commands/{command}", body=body, idempotency_key=idempotency_key)


def _add_page_arguments(parser: argparse.ArgumentParser, *, include_search: bool = True) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    if include_search:
        parser.add_argument("--q")
    parser.add_argument("--fields")


def _page_query(args: argparse.Namespace, *, search: str | None = None) -> dict[str, str]:
    query = {
        "limit": str(args.limit),
        "sort": str(args.sort),
        "direction": str(args.direction),
    }
    if args.cursor:
        query["cursor"] = str(args.cursor)
    raw_search = search if search is not None else getattr(args, "q", None)
    if raw_search:
        query["q"] = str(raw_search)
    if args.fields:
        query["fields"] = str(args.fields)
    return query


def _set_filter(query: dict[str, str], field: str, value: object | None) -> None:
    if value is not None:
        query[f"filter[{field}]"] = str(value)


def _copy_optional(body: dict[str, JsonValue], field: str, value: object | None) -> None:
    if value is not None:
        body[field] = str(value)


def _parse_json(raw: str, flag: str) -> JsonValue:
    try:
        return cast(JsonValue, json.loads(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileError(f"{flag} must contain valid JSON") from exc


def _parse_object(raw: str, flag: str) -> dict[str, JsonValue]:
    value = _parse_json(raw, flag)
    if not isinstance(value, dict):
        raise ProfileError(f"{flag} must be a JSON object")
    return value


def _parse_array(raw: str, flag: str) -> list[JsonValue]:
    value = _parse_json(raw, flag)
    if not isinstance(value, list):
        raise ProfileError(f"{flag} must be a JSON array")
    return value


def _segment(value: str) -> str:
    return quote(value, safe="")


__all__ = [
    "add_memory_knowledge_parsers",
    "execute_knowledge",
    "execute_memory",
]
