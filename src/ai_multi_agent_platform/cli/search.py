"""CLI adapter for the canonical platform-wide Search API."""

from __future__ import annotations

import argparse

from .client import ClientResponse, ControlPlaneClient

_SEARCH_MODES = ("exact", "keyword", "metadata", "semantic", "hybrid")
_SEARCH_SORTS = ("relevance", "id", "updated_at")
_TASK_PRIORITIES = ("low", "normal", "high", "urgent")


def add_search_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register `platform search` without duplicating Search semantics in the CLI."""

    search = areas.add_parser("search", help="search authorized canonical platform resources")
    search.set_defaults(area="search")
    search.add_argument("query_text", nargs="?", help="free-text search query")
    search.add_argument("--id", dest="exact_id", help="exact canonical resource ID")
    search.add_argument(
        "--type",
        dest="resource_types",
        action="append",
        default=[],
        help="resource type; repeat or use comma-separated values",
    )
    search.add_argument("--project-id", help="scope results to a Project")
    search.add_argument("--workspace-id", help="scope results to a Workspace")
    search.add_argument(
        "--status",
        dest="statuses",
        action="append",
        default=[],
        help="status filter; repeat or use comma-separated values",
    )
    search.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="tag filter; repeat or use comma-separated values",
    )
    search.add_argument(
        "--priority",
        dest="priorities",
        action="append",
        default=[],
        help="Task priority; repeat or use comma-separated values",
    )
    search.add_argument("--due-after", help="inclusive timezone-aware Task due lower bound")
    search.add_argument("--due-before", help="inclusive timezone-aware Task due upper bound")
    search.add_argument(
        "--assignment-state",
        choices=["assigned", "unassigned"],
        help="filter Tasks by assignment state",
    )
    search.add_argument("--responsible-id", help="filter Tasks by responsible actor/team ID")
    search.add_argument(
        "--agent-assignment-id",
        help="filter Tasks by assigned canonical Agent/AgentTeam ID",
    )
    search.add_argument(
        "--blocked",
        choices=["true", "false"],
        help="filter Tasks by blocked/unblocked state",
    )
    search.add_argument(
        "--overdue",
        choices=["true", "false"],
        help="filter Tasks by overdue state",
    )
    search.add_argument("--dependency-id", help="filter Tasks by canonical dependency Task ID")
    search.add_argument(
        "--source",
        dest="sources",
        action="append",
        default=[],
        help="source filter; repeat or use comma-separated values",
    )
    search.add_argument(
        "--provider",
        dest="providers",
        action="append",
        default=[],
        help="provider filter; repeat or use comma-separated values",
    )
    search.add_argument("--updated-after", help="inclusive timezone-aware ISO-8601 lower bound")
    search.add_argument("--updated-before", help="inclusive timezone-aware ISO-8601 upper bound")
    search.add_argument(
        "--mode", choices=_SEARCH_MODES, help="search mode; semantic/hybrid are optional"
    )
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--cursor")
    search.add_argument("--sort", choices=_SEARCH_SORTS, default="relevance")
    search.add_argument("--direction", choices=["asc", "desc"], default="desc")


def execute_search(args: argparse.Namespace, client: ControlPlaneClient) -> ClientResponse:
    """Forward the CLI query to the canonical Control Plane Search endpoint."""

    return client.get("/search", query=_search_query(args))


def _search_query(args: argparse.Namespace) -> dict[str, str]:
    query: dict[str, str] = {
        "limit": str(args.limit),
        "sort": str(args.sort),
        "direction": str(args.direction),
    }
    for field, value in (
        ("q", args.query_text),
        ("id", args.exact_id),
        ("project_id", args.project_id),
        ("workspace_id", args.workspace_id),
        ("due_after", args.due_after),
        ("due_before", args.due_before),
        ("assignment_state", args.assignment_state),
        ("responsible_id", args.responsible_id),
        ("agent_assignment_id", args.agent_assignment_id),
        ("blocked", args.blocked),
        ("overdue", args.overdue),
        ("dependency_id", args.dependency_id),
        ("updated_after", args.updated_after),
        ("updated_before", args.updated_before),
        ("mode", args.mode),
        ("cursor", args.cursor),
    ):
        if value:
            query[field] = str(value)

    for field, values in (
        ("type", args.resource_types),
        ("status", args.statuses),
        ("tag", args.tags),
        ("priority", args.priorities),
        ("source", args.sources),
        ("provider", args.providers),
    ):
        combined = _csv(values)
        if combined:
            query[field] = combined
    return query


def _csv(values: list[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            value = part.strip()
            if value and value not in seen:
                seen.add(value)
                normalized.append(value)
    return ",".join(normalized)
