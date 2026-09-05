"""Issue #82 CLI composition for provider-neutral repository operations.

Repository commands remain API-first: the CLI only calls the canonical Control Plane
resource and command surfaces registered by ``repositories.control_plane``. Existing
CLI areas are delegated unchanged to the authenticated issue #214 composition.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import (
    APIClientError,
    ClientOptions,
    ClientResponse,
    ControlPlaneClient,
    HTTPTransport,
    TransportError,
    UrllibTransport,
)
from .credentials import AuthenticatedTransport, CredentialStore
from .issue_214 import run_cli as issue_214_run_cli
from .profiles import CLIProfile, ProfileError, ProfileStore, default_config_path
from .render import Renderer

_READ_COMMANDS = frozenset({"branches", "tags", "commits", "status", "diff"})
_MUTATING_COMMANDS = frozenset(
    {
        "fetch",
        "branch-create",
        "checkout",
        "commit",
        "push",
        "attach-local",
        "detach",
    }
)


def main() -> int:
    return run_cli()


def run_cli(
    argv: list[str] | None = None,
    *,
    transport: HTTPTransport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if _requested_area(arguments) != "repository":
        return issue_214_run_cli(
            arguments,
            transport=transport,
            stdout=stdout,
            stderr=stderr,
            stdin=stdin,
        )

    parser = _build_parser()
    args = parser.parse_args(arguments)
    renderer = Renderer(
        json_mode=bool(args.json),
        verbose=bool(args.verbose),
        stdout=stdout,
        stderr=stderr,
    )

    try:
        profiles = ProfileStore.load(Path(args.config).expanduser())
        profile_name, profile = profiles.resolve(args.profile)
        if args.endpoint is not None:
            profile = CLIProfile(
                endpoint=args.endpoint,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            )
        credentials = CredentialStore.load(profiles.path).get(profile_name)
        base_transport = transport or UrllibTransport()
        client = ControlPlaneClient(
            ClientOptions(
                endpoint=profile.endpoint,
                timeout=args.timeout,
                retries=args.retries,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            ),
            transport=AuthenticatedTransport(base_transport, credentials),
        )
        response = _execute_repository(args, client)
        renderer.success(response)
        return 0
    except (ProfileError, ValueError) as exc:
        renderer.error(ProfileError(str(exc)))
        return 2
    except APIClientError as exc:
        renderer.error(exc)
        return 3
    except TransportError as exc:
        renderer.error(exc)
        return 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--profile")
    parser.add_argument("--endpoint")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm repository commands with local or external side effects",
    )

    areas = parser.add_subparsers(dest="area", required=True)
    repository = areas.add_parser("repository", help="manage canonical repositories and Git state")
    commands = repository.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list authorized canonical repositories")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--cursor")
    list_parser.add_argument("--q")
    list_parser.add_argument("--connection-id")

    show = commands.add_parser("show", help="show one canonical repository")
    show.add_argument("repository_id")

    for command in ("status", "branches", "tags"):
        action = commands.add_parser(command, help=f"inspect repository {command}")
        action.add_argument("repository_id")
        action.add_argument("--approval-id")

    commits = commands.add_parser("commits", help="inspect canonical commit history")
    commits.add_argument("repository_id")
    commits.add_argument("--revision", default="HEAD")
    commits.add_argument("--limit", type=int, default=50)
    commits.add_argument("--approval-id")

    diff = commands.add_parser("diff", help="inspect repository workspace diff")
    diff.add_argument("repository_id")
    diff.add_argument("--base-revision")
    diff.add_argument("--approval-id")

    fetch = commands.add_parser("fetch", help="fetch repository refs through the provider")
    fetch.add_argument("repository_id")
    _add_mutation_arguments(fetch)

    branch = commands.add_parser("branch-create", help="create a repository branch")
    branch.add_argument("repository_id")
    branch.add_argument("name")
    branch.add_argument("--start-revision", default="HEAD")
    branch.add_argument("--checkout", action="store_true")
    _add_mutation_arguments(branch)

    checkout = commands.add_parser("checkout", help="checkout a canonical revision/ref")
    checkout.add_argument("repository_id")
    checkout.add_argument("revision")
    _add_mutation_arguments(checkout)

    commit = commands.add_parser("commit", help="create a local repository commit")
    commit.add_argument("repository_id")
    commit.add_argument("--message", required=True)
    commit.add_argument("--author-name", required=True)
    commit.add_argument("--author-email", required=True)
    _add_mutation_arguments(commit)

    push = commands.add_parser("push", help="push repository changes through the provider")
    push.add_argument("repository_id")
    push.add_argument("--remote", default="origin")
    push.add_argument("--refspec")
    _add_mutation_arguments(push)

    attach = commands.add_parser(
        "attach-local",
        help="attach or initialize a managed local repository for a project",
    )
    attach.add_argument("project_id")
    attach.add_argument("name")
    attach.add_argument("--initialize", action="store_true")
    attach.add_argument("--default-branch", default="main")
    _add_mutation_arguments(attach)

    discover = commands.add_parser(
        "discover",
        help="discover repositories exposed by one canonical Connection",
    )
    discover.add_argument("connection_id")
    discover.add_argument("--provider-id", required=True)
    discover.add_argument("--attach", action="store_true")
    discover.add_argument("--approval-id")
    discover.add_argument("--idempotency-key")

    detach = commands.add_parser(
        "detach",
        help="detach canonical repository metadata without deleting provider content",
    )
    detach.add_argument("repository_id")
    _add_mutation_arguments(detach)

    return parser


def _add_mutation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--approval-id")
    parser.add_argument("--idempotency-key")


def _execute_repository(args: argparse.Namespace, client: ControlPlaneClient) -> ClientResponse:
    command = str(args.command)
    if command in _MUTATING_COMMANDS or (command == "discover" and bool(args.attach)):
        _require_confirmation(args, command)

    if command == "list":
        query = _list_query(args)
        return client.get("/repositories", query=query)
    if command == "show":
        return client.get(f"/repositories/{quote(args.repository_id, safe='')}")
    if command in _READ_COMMANDS:
        payload: dict[str, JsonValue] = {}
        if command == "commits":
            payload["revision"] = args.revision
            payload["limit"] = args.limit
        elif command == "diff" and args.base_revision is not None:
            payload["base_revision"] = args.base_revision
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(client, f"repository.{command}", args.repository_id, payload)
    if command == "fetch":
        return _repository_command(
            client,
            "repository.fetch",
            args.repository_id,
            _approval_payload(args.approval_id),
            idempotency_key=args.idempotency_key,
        )
    if command == "branch-create":
        payload = {
            "name": args.name,
            "start_revision": args.start_revision,
            "checkout": bool(args.checkout),
        }
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(
            client,
            "repository.branch.create",
            args.repository_id,
            payload,
            idempotency_key=args.idempotency_key,
        )
    if command == "checkout":
        payload = {"revision": args.revision}
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(
            client,
            "repository.checkout",
            args.repository_id,
            payload,
            idempotency_key=args.idempotency_key,
        )
    if command == "commit":
        payload = {
            "message": args.message,
            "author_name": args.author_name,
            "author_email": args.author_email,
        }
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(
            client,
            "repository.commit",
            args.repository_id,
            payload,
            idempotency_key=args.idempotency_key,
        )
    if command == "push":
        payload = {"remote": args.remote}
        _optional_payload(payload, "refspec", args.refspec)
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(
            client,
            "repository.push",
            args.repository_id,
            payload,
            idempotency_key=args.idempotency_key,
        )
    if command == "attach-local":
        payload = {
            "name": args.name,
            "initialize": bool(args.initialize),
            "default_branch": args.default_branch,
        }
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(
            client,
            "repository.local.attach",
            args.project_id,
            payload,
            idempotency_key=args.idempotency_key,
        )
    if command == "discover":
        payload = {"provider_id": args.provider_id, "attach": bool(args.attach)}
        _optional_payload(payload, "approval_id", args.approval_id)
        return _repository_command(
            client,
            "repository.discover",
            args.connection_id,
            payload,
            idempotency_key=args.idempotency_key,
        )
    if command == "detach":
        return _repository_command(
            client,
            "repository.detach",
            args.repository_id,
            _approval_payload(args.approval_id),
            idempotency_key=args.idempotency_key,
        )
    raise ValueError(f"unsupported repository command: {command}")


def _repository_command(
    client: ControlPlaneClient,
    command: str,
    resource_ref: str,
    payload: Mapping[str, JsonValue],
    *,
    idempotency_key: str | None = None,
) -> ClientResponse:
    body: dict[str, JsonValue] = {"resource_ref": resource_ref}
    body.update(payload)
    return client.post(
        f"/commands/{quote(command, safe='.')}",
        body=body,
        idempotency_key=idempotency_key,
    )


def _list_query(args: argparse.Namespace) -> dict[str, str]:
    if args.limit < 1 or args.limit > 200:
        raise ValueError("repository list --limit must be between 1 and 200")
    query = {"limit": str(args.limit)}
    if args.cursor is not None:
        query["cursor"] = args.cursor
    if args.q is not None:
        query["q"] = args.q
    if args.connection_id is not None:
        query["filter.connection_id"] = args.connection_id
    return query


def _approval_payload(approval_id: str | None) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {}
    _optional_payload(payload, "approval_id", approval_id)
    return payload


def _optional_payload(payload: dict[str, JsonValue], key: str, value: str | None) -> None:
    if value is not None:
        payload[key] = value


def _require_confirmation(args: argparse.Namespace, command: str) -> None:
    if not bool(args.yes):
        raise ValueError(
            f"repository {command} has side effects; rerun with global --yes after reviewing it"
        )


def _requested_area(arguments: list[str]) -> str | None:
    options_with_value = {"--config", "--profile", "--endpoint", "--timeout", "--retries"}
    skip_next = False
    for token in arguments:
        if skip_next:
            skip_next = False
            continue
        if token in options_with_value:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None
