"""API-first CLI surface for provider-neutral repositories and Git operations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

Confirmation = Callable[[argparse.Namespace, str, str], None]


def add_repository_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register repository commands without importing repository providers or adapters."""

    repository = areas.add_parser(
        "repository",
        help="inspect and operate provider-neutral repositories through the Control Plane",
    )
    repository.set_defaults(area="repository")
    commands = repository.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list attached repositories")
    _add_page_arguments(list_parser)
    show = commands.add_parser("show", help="show one attached repository")
    show.add_argument("repository_id")

    attach = commands.add_parser(
        "attach-local", help="attach or initialize a managed local Git repository"
    )
    attach.add_argument("project_id")
    attach.add_argument("--name", required=True)
    attach.add_argument("--initialize", action="store_true")
    attach.add_argument("--default-branch", default="main")
    attach.add_argument("--idempotency-key")

    discover = commands.add_parser(
        "discover",
        help="discover repositories through one configured repository provider connection",
    )
    discover.add_argument("connection_id")
    discover.add_argument("--provider-id", required=True)
    discover.add_argument("--attach", action="store_true")
    discover.add_argument("--idempotency-key")

    detach = commands.add_parser("detach", help="detach one repository from platform management")
    detach.add_argument("repository_id")
    detach.add_argument("--idempotency-key")

    for name in ("branches", "tags", "status"):
        action = commands.add_parser(name, help=f"inspect repository {name}")
        action.add_argument("repository_id")
        action.add_argument("--idempotency-key")

    commits = commands.add_parser("commits", help="inspect repository commit history")
    commits.add_argument("repository_id")
    commits.add_argument("--revision", default="HEAD")
    commits.add_argument("--limit", type=int, default=50)
    commits.add_argument("--idempotency-key")

    diff = commands.add_parser("diff", help="inspect repository working-tree diff")
    diff.add_argument("repository_id")
    diff.add_argument("--base-revision")
    diff.add_argument("--idempotency-key")

    fetch = commands.add_parser("fetch", help="fetch repository refs from the configured remote")
    fetch.add_argument("repository_id")
    fetch.add_argument("--idempotency-key")

    create_branch = commands.add_parser("create-branch", help="create a local repository branch")
    create_branch.add_argument("repository_id")
    create_branch.add_argument("--name", required=True)
    create_branch.add_argument("--start-revision", default="HEAD")
    create_branch.add_argument("--checkout", action="store_true")
    create_branch.add_argument("--idempotency-key")

    checkout = commands.add_parser("checkout", help="checkout a repository revision")
    checkout.add_argument("repository_id")
    checkout.add_argument("revision")
    checkout.add_argument("--idempotency-key")

    commit = commands.add_parser("commit", help="create a repository commit")
    commit.add_argument("repository_id")
    commit.add_argument("--message", required=True)
    commit.add_argument("--author-name", required=True)
    commit.add_argument("--author-email", required=True)
    commit.add_argument("--idempotency-key")

    push = commands.add_parser("push", help="push repository refs to the configured remote")
    push.add_argument("repository_id")
    push.add_argument("--remote", default="origin")
    push.add_argument("--refspec")
    push.add_argument("--idempotency-key")

    issue = commands.add_parser(
        "issue", help="inspect and update provider-neutral repository issues"
    )
    issue_commands = issue.add_subparsers(dest="issue_command", required=True)
    issue_show = issue_commands.add_parser(
        "show", help="show a canonical repository issue reference"
    )
    issue_show.add_argument("repository_id")
    issue_show.add_argument("--resource-json", required=True)
    issue_show.add_argument("--idempotency-key")
    issue_open = issue_commands.add_parser("open", help="open a repository issue")
    issue_open.add_argument("repository_id")
    issue_open.add_argument("--title", required=True)
    issue_open.add_argument("--body")
    issue_open.add_argument("--idempotency-key")
    issue_update = issue_commands.add_parser("update", help="update a repository issue")
    issue_update.add_argument("repository_id")
    issue_update.add_argument("--resource-json", required=True)
    issue_update.add_argument("--title")
    issue_update.add_argument("--body")
    issue_update.add_argument("--state", choices=["open", "closed", "unknown"])
    issue_update.add_argument("--idempotency-key")

    change_request = commands.add_parser(
        "change-request",
        help="inspect and update provider-neutral repository change requests",
    )
    change_request_commands = change_request.add_subparsers(
        dest="change_request_command",
        required=True,
    )
    change_show = change_request_commands.add_parser(
        "show",
        help="show a canonical repository change-request reference",
    )
    change_show.add_argument("repository_id")
    change_show.add_argument("--resource-json", required=True)
    change_show.add_argument("--idempotency-key")
    change_open = change_request_commands.add_parser(
        "open", help="open a repository change request"
    )
    change_open.add_argument("repository_id")
    change_open.add_argument("--title", required=True)
    change_open.add_argument("--head-ref", required=True)
    change_open.add_argument("--base-ref", required=True)
    change_open.add_argument("--body")
    change_open.add_argument("--idempotency-key")
    change_update = change_request_commands.add_parser(
        "update",
        help="update a repository change request",
    )
    change_update.add_argument("repository_id")
    change_update.add_argument("--resource-json", required=True)
    change_update.add_argument("--title")
    change_update.add_argument("--body")
    change_update.add_argument("--state", choices=["open", "draft", "closed", "merged", "unknown"])
    change_update.add_argument("--idempotency-key")


def execute_repository(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    """Execute repository commands through canonical resource/command endpoints only."""

    if args.command == "list":
        return client.get("/repositories", query=_page_query(args))
    if args.command == "show":
        return client.get(f"/repositories/{_segment(args.repository_id)}")
    if args.command == "attach-local":
        confirm(args, "attach local repository", args.project_id)
        return _command(
            client,
            "repository.local.attach",
            args.project_id,
            {
                "name": args.name,
                "initialize": bool(args.initialize),
                "default_branch": args.default_branch,
            },
            args.idempotency_key,
        )
    if args.command == "discover":
        if args.attach:
            confirm(args, "discover and attach repositories", args.connection_id)
        return _command(
            client,
            "repository.discover",
            args.connection_id,
            {"provider_id": args.provider_id, "attach": bool(args.attach)},
            args.idempotency_key,
        )
    if args.command == "detach":
        confirm(args, "detach repository", args.repository_id)
        return _command(
            client,
            "repository.detach",
            args.repository_id,
            {},
            args.idempotency_key,
        )
    if args.command in {"branches", "tags", "status"}:
        return _command(
            client,
            f"repository.{args.command}",
            args.repository_id,
            {},
            args.idempotency_key,
        )
    if args.command == "commits":
        if args.limit <= 0:
            raise ProfileError("--limit must be greater than zero")
        return _command(
            client,
            "repository.commits",
            args.repository_id,
            {"revision": args.revision, "limit": args.limit},
            args.idempotency_key,
        )
    if args.command == "diff":
        return _command(
            client,
            "repository.diff",
            args.repository_id,
            _present({"base_revision": args.base_revision}),
            args.idempotency_key,
        )
    if args.command == "fetch":
        confirm(args, "fetch repository", args.repository_id)
        return _command(
            client,
            "repository.fetch",
            args.repository_id,
            {},
            args.idempotency_key,
        )
    if args.command == "create-branch":
        confirm(args, "create repository branch", args.repository_id)
        return _command(
            client,
            "repository.branch.create",
            args.repository_id,
            {
                "name": args.name,
                "start_revision": args.start_revision,
                "checkout": bool(args.checkout),
            },
            args.idempotency_key,
        )
    if args.command == "checkout":
        confirm(args, "checkout repository revision", args.repository_id)
        return _command(
            client,
            "repository.checkout",
            args.repository_id,
            {"revision": args.revision},
            args.idempotency_key,
        )
    if args.command == "commit":
        confirm(args, "commit repository changes", args.repository_id)
        return _command(
            client,
            "repository.commit",
            args.repository_id,
            {
                "message": args.message,
                "author_name": args.author_name,
                "author_email": args.author_email,
            },
            args.idempotency_key,
        )
    if args.command == "push":
        confirm(args, "push repository", args.repository_id)
        return _command(
            client,
            "repository.push",
            args.repository_id,
            _present({"remote": args.remote, "refspec": args.refspec}),
            args.idempotency_key,
        )
    if args.command == "issue":
        return _execute_issue(args, client, confirm)
    if args.command == "change-request":
        return _execute_change_request(args, client, confirm)
    raise ProfileError(f"unsupported repository command: {args.command}")


def _execute_issue(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    if args.issue_command == "show":
        return _command(
            client,
            "repository.issue.read",
            args.repository_id,
            {"resource": _json_object(args.resource_json, "--resource-json")},
            args.idempotency_key,
        )
    if args.issue_command == "open":
        confirm(args, "open repository issue", args.repository_id)
        return _command(
            client,
            "repository.issue.open",
            args.repository_id,
            _present({"title": args.title, "body": args.body}),
            args.idempotency_key,
        )
    if args.issue_command == "update":
        confirm(args, "update repository issue", args.repository_id)
        changes = _present({"title": args.title, "body": args.body, "state": args.state})
        if not changes:
            raise ProfileError("repository issue update requires --title, --body, or --state")
        return _command(
            client,
            "repository.issue.update",
            args.repository_id,
            {
                "resource": _json_object(args.resource_json, "--resource-json"),
                **changes,
            },
            args.idempotency_key,
        )
    raise ProfileError(f"unsupported repository issue command: {args.issue_command}")


def _execute_change_request(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    if args.change_request_command == "show":
        return _command(
            client,
            "repository.change_request.read",
            args.repository_id,
            {"resource": _json_object(args.resource_json, "--resource-json")},
            args.idempotency_key,
        )
    if args.change_request_command == "open":
        confirm(args, "open repository change request", args.repository_id)
        return _command(
            client,
            "repository.change_request.open",
            args.repository_id,
            _present(
                {
                    "title": args.title,
                    "head_ref": args.head_ref,
                    "base_ref": args.base_ref,
                    "body": args.body,
                }
            ),
            args.idempotency_key,
        )
    if args.change_request_command == "update":
        confirm(args, "update repository change request", args.repository_id)
        changes = _present({"title": args.title, "body": args.body, "state": args.state})
        if not changes:
            raise ProfileError(
                "repository change-request update requires --title, --body, or --state"
            )
        return _command(
            client,
            "repository.change_request.update",
            args.repository_id,
            {
                "resource": _json_object(args.resource_json, "--resource-json"),
                **changes,
            },
            args.idempotency_key,
        )
    raise ProfileError(
        f"unsupported repository change-request command: {args.change_request_command}"
    )


def _command(
    client: ControlPlaneClient,
    command: str,
    resource_ref: str,
    payload: dict[str, JsonValue],
    idempotency_key: str | None,
) -> ClientResponse:
    return client.post(
        f"/commands/{command}",
        body={"resource_ref": resource_ref, **payload},
        idempotency_key=idempotency_key,
    )


def _json_object(raw: str, option: str) -> dict[str, JsonValue]:
    try:
        value: JsonValue = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileError(f"{option} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ProfileError(f"{option} must be a JSON object")
    return value


def _present(values: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: value for key, value in values.items() if value is not None}


def _add_page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    if args.limit <= 0:
        raise ProfileError("--limit must be greater than zero")
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
