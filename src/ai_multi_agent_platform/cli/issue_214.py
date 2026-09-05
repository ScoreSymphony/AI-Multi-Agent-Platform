"""Issue #214 CLI composition for authentication and Approval decisions.

The established CLI remains the implementation for every earlier command.  This layer
adds the dependency-bound authentication and approval surfaces and injects resolved
credentials at the HTTP transport boundary for all existing API-first commands.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO, cast
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
from .credentials import (
    AuthenticatedTransport,
    CapturingTransport,
    CredentialState,
    CredentialStore,
    session_cookie_from_response,
)
from .main import run_cli as legacy_run_cli
from .profiles import CLIProfile, ProfileError, ProfileStore
from .render import Renderer


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
    area = _requested_area(arguments)
    if area not in {"auth", "approval", "__help__"}:
        return _run_legacy_authenticated(
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
    input_stream = stdin or sys.stdin

    try:
        config_path = Path(args.config).expanduser() if args.config else None
        profiles = ProfileStore.load(config_path)
        profile_name, profile = profiles.resolve(args.profile)
        if args.endpoint is not None:
            profile = CLIProfile(
                endpoint=args.endpoint,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            )
        credentials = CredentialStore.load(profiles.path)
        base_transport = transport or UrllibTransport()

        if args.area == "auth":
            return _execute_auth(
                args,
                profile_name=profile_name,
                profile=profile,
                credentials=credentials,
                base_transport=base_transport,
                renderer=renderer,
                stdin=input_stream,
            )

        state = credentials.get(profile_name)
        client = _client(
            args,
            profile,
            AuthenticatedTransport(base_transport, state),
        )
        response = _execute_approval(args, client, stdin=input_stream, stdout=renderer.stdout)
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


def _run_legacy_authenticated(
    arguments: list[str],
    *,
    transport: HTTPTransport | None,
    stdout: TextIO | None,
    stderr: TextIO | None,
    stdin: TextIO | None,
) -> int:
    base_transport = transport or UrllibTransport()
    try:
        common, _ = _common_parser().parse_known_args(arguments)
        config_path = Path(common.config).expanduser() if common.config else None
        profiles = ProfileStore.load(config_path)
        profile_name, _ = profiles.resolve(common.profile)
        state = CredentialStore.load(profiles.path).get(profile_name)
        resolved_transport: HTTPTransport = AuthenticatedTransport(base_transport, state)
    except (ProfileError, ValueError):
        # Preserve the established parser/configuration error contract.  The legacy CLI
        # will diagnose the invalid input itself rather than this wrapper masking it.
        resolved_transport = base_transport
    return legacy_run_cli(
        arguments,
        transport=resolved_transport,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform")
    _add_common_arguments(parser)
    areas = parser.add_subparsers(dest="area", required=True)

    auth = areas.add_parser("auth", help="manage canonical authentication and credentials")
    auth_commands = auth.add_subparsers(dest="command", required=True)
    login = auth_commands.add_parser("login", help="create a canonical browser session")
    login.add_argument("--username", required=True)
    login.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of an interactive hidden prompt",
    )
    auth_commands.add_parser("me", help="inspect the authenticated actor/profile")
    auth_commands.add_parser("status", help="inspect local credential state without secrets")
    auth_commands.add_parser(
        "logout", help="revoke the active browser session or clear local token"
    )

    session = auth_commands.add_parser("session", help="inspect and manage browser sessions")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    session_commands.add_parser("list", help="list canonical sessions")
    session_commands.add_parser("renew", help="rotate the active browser session")
    session_revoke = session_commands.add_parser("revoke", help="revoke one canonical session")
    session_revoke.add_argument("session_id")

    credential = auth_commands.add_parser("credential", help="manage personal API credentials")
    credential_commands = credential.add_subparsers(dest="credential_command", required=True)
    credential_commands.add_parser("list", help="list safe credential metadata")
    credential_create = credential_commands.add_parser(
        "create",
        help="create and securely activate a personal credential for this profile",
    )
    credential_create.add_argument("--purpose", required=True)
    credential_create.add_argument("--expires-at")
    credential_create.add_argument(
        "--scope-json",
        help="canonical credential scope JSON object as exposed by #36",
    )
    credential_revoke = credential_commands.add_parser("revoke", help="revoke one credential")
    credential_revoke.add_argument("credential_id")

    token = auth_commands.add_parser(
        "token",
        help="activate or clear an already-issued bearer/service credential",
    )
    token_commands = token.add_subparsers(dest="token_command", required=True)
    token_activate = token_commands.add_parser(
        "activate", help="read and validate a token from stdin"
    )
    token_activate.add_argument(
        "--token-stdin",
        action="store_true",
        required=True,
        help="read the bearer token from stdin",
    )
    token_commands.add_parser("clear", help="remove the locally stored bearer credential")

    approval = areas.add_parser("approval", help="inspect and decide canonical Approvals")
    approval_commands = approval.add_subparsers(dest="command", required=True)
    approval_list = approval_commands.add_parser("list", help="list canonical Approvals")
    _add_pagination_arguments(approval_list)
    approval_show = approval_commands.add_parser("show", help="show one canonical Approval")
    approval_show.add_argument("approval_id")
    for decision in ("approve", "deny"):
        decision_parser = approval_commands.add_parser(
            decision,
            help=f"{decision} one exact-action Approval through the #15 gate",
        )
        decision_parser.add_argument("approval_id")
        decision_parser.add_argument("--comment")
        decision_parser.add_argument("--idempotency-key")

    return parser


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    _add_common_arguments(parser)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument("--profile")
    parser.add_argument("--endpoint")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--yes", action="store_true")


def _requested_area(arguments: list[str]) -> str | None:
    try:
        _, remaining = _common_parser().parse_known_args(arguments)
    except SystemExit:
        return None
    if remaining and remaining[0] in {"-h", "--help"}:
        return "__help__"
    return remaining[0] if remaining else None


def _client(
    args: argparse.Namespace,
    profile: CLIProfile,
    transport: HTTPTransport,
) -> ControlPlaneClient:
    return ControlPlaneClient(
        ClientOptions(
            endpoint=profile.endpoint,
            timeout=float(args.timeout),
            retries=int(args.retries),
            principal_ref=profile.principal_ref,
            owner_type=profile.owner_type,
            owner_id=profile.owner_id,
        ),
        transport=transport,
    )


def _execute_auth(
    args: argparse.Namespace,
    *,
    profile_name: str,
    profile: CLIProfile,
    credentials: CredentialStore,
    base_transport: HTTPTransport,
    renderer: Renderer,
    stdin: TextIO,
) -> int:
    state = credentials.get(profile_name)

    if args.command == "status":
        renderer.local_success(_safe_local_auth_status(profile_name, state))
        return 0

    if args.command == "login":
        password = _read_secret(
            stdin,
            from_stdin=bool(args.password_stdin),
            prompt="Password: ",
        )
        capture = CapturingTransport(base_transport)
        response = _client(args, profile, capture).post(
            "/auth/login",
            body={"username": args.username, "password": password},
        )
        cookie = session_cookie_from_response(capture.last_response)
        body = _object_body(response)
        csrf_token = _body_string(body, "csrf_token")
        expires_at = _body_string(body, "expires_at")
        credentials.set(
            profile_name,
            CredentialState(
                mode="session",
                session_cookie=cookie,
                csrf_token=csrf_token,
                expires_at=expires_at,
            ),
        )
        renderer.success(
            _response_with_body(
                response,
                {
                    "authenticated": True,
                    "profile": profile_name,
                    "actor": body.get("actor"),
                    "expires_at": expires_at,
                },
            )
        )
        return 0

    if args.command == "token":
        if args.token_command == "clear":
            credentials.clear(profile_name)
            renderer.local_success({"profile": profile_name, "authenticated": False})
            return 0
        token = _read_secret(stdin, from_stdin=True, prompt="Bearer token: ")
        candidate = CredentialState(mode="bearer", bearer_token=token)
        response = _client(
            args,
            profile,
            AuthenticatedTransport(base_transport, candidate),
        ).get("/auth/me")
        body = _object_body(response)
        credential_id = body.get("credential_id")
        credentials.set(
            profile_name,
            CredentialState(
                mode="bearer",
                bearer_token=token,
                credential_id=credential_id if isinstance(credential_id, str) else None,
            ),
        )
        renderer.success(
            _response_with_body(
                response,
                {
                    "authenticated": True,
                    "profile": profile_name,
                    "actor": cast(JsonValue, body),
                    "credential_stored": True,
                },
            )
        )
        return 0

    client = _client(
        args,
        profile,
        AuthenticatedTransport(base_transport, state),
    )
    if args.command == "me":
        renderer.success(client.get("/auth/me"))
        return 0

    if args.command == "logout":
        if state is None:
            credentials.clear(profile_name)
            renderer.local_success({"profile": profile_name, "authenticated": False})
            return 0
        if state.mode == "bearer":
            credentials.clear(profile_name)
            renderer.local_success(
                {
                    "profile": profile_name,
                    "authenticated": False,
                    "credential_revoked": False,
                    "message": "local bearer credential cleared; revoke it explicitly if needed",
                }
            )
            return 0
        response = client.post("/auth/logout")
        credentials.clear(profile_name)
        renderer.success(response)
        return 0

    if args.command == "session":
        if args.session_command == "list":
            renderer.success(client.get("/auth/sessions"))
            return 0
        if args.session_command == "renew":
            capture = CapturingTransport(AuthenticatedTransport(base_transport, state))
            response = _client(args, profile, capture).post("/auth/session:renew")
            body = _object_body(response)
            credentials.set(
                profile_name,
                CredentialState(
                    mode="session",
                    session_cookie=session_cookie_from_response(capture.last_response),
                    csrf_token=_body_string(body, "csrf_token"),
                    expires_at=_body_string(body, "expires_at"),
                ),
            )
            renderer.success(
                _response_with_body(
                    response,
                    {
                        "renewed": True,
                        "profile": profile_name,
                        "expires_at": body.get("expires_at"),
                    },
                )
            )
            return 0
        _require_confirmation(
            args,
            f"revoke session {args.session_id}",
            stdin=stdin,
            stdout=renderer.stdout,
        )
        renderer.success(client.post(f"/auth/sessions/{_segment(args.session_id)}:revoke"))
        return 0

    if args.command == "credential":
        if args.credential_command == "list":
            renderer.success(client.get("/auth/credentials"))
            return 0
        if args.credential_command == "create":
            credential_body: dict[str, JsonValue] = {"purpose": args.purpose}
            if args.expires_at:
                credential_body["expires_at"] = args.expires_at
            if args.scope_json:
                scope = json.loads(args.scope_json)
                if not isinstance(scope, dict):
                    raise ProfileError("--scope-json must decode to a JSON object")
                credential_body["scope"] = cast(dict[str, JsonValue], scope)
            response = client.post("/auth/credentials", body=credential_body)
            response_body = _object_body(response)
            secret = _body_string(response_body, "secret")
            credential_id = _body_string(response_body, "id")
            credential_expires_at = response_body.get("expires_at")
            credentials.set(
                profile_name,
                CredentialState(
                    mode="bearer",
                    bearer_token=secret,
                    credential_id=credential_id,
                    expires_at=(
                        credential_expires_at if isinstance(credential_expires_at, str) else None
                    ),
                ),
            )
            safe_body = dict(response_body)
            safe_body.pop("secret", None)
            safe_body["credential_stored"] = True
            safe_body["profile"] = profile_name
            renderer.success(_response_with_body(response, safe_body))
            return 0

        _require_confirmation(
            args,
            f"revoke credential {args.credential_id}",
            stdin=stdin,
            stdout=renderer.stdout,
        )
        response = client.post(f"/auth/credentials/{_segment(args.credential_id)}:revoke")
        if state is not None and state.credential_id == args.credential_id:
            credentials.clear(profile_name)
        renderer.success(response)
        return 0

    raise ProfileError(f"unsupported auth command: {args.command}")


def _execute_approval(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    *,
    stdin: TextIO,
    stdout: TextIO,
) -> ClientResponse:
    if args.command == "list":
        return client.get("/approvals", query=_page_query(args))
    approval_id = _segment(args.approval_id)
    if args.command == "show":
        return client.get(f"/approvals/{approval_id}")

    current = client.get(f"/approvals/{approval_id}")
    approval = _object_body(current)
    digest = _body_string(approval, "requested_action_digest")
    status = _body_string(approval, "status")
    if status != "pending":
        raise ProfileError(f"approval is not pending: {status}")
    action = _body_string(approval, "action")
    resource_type = _body_string(approval, "resource_type")
    resource_id = _body_string(approval, "resource_id")
    risk = _body_string(approval, "risk")
    policy_id = _body_string(approval, "policy_id")
    context = (
        f"{args.command} approval {args.approval_id} "
        f"[action={action} resource={resource_type}:{resource_id} "
        f"risk={risk} policy={policy_id} digest={digest}]"
    )
    _require_confirmation(args, context, stdin=stdin, stdout=stdout)
    body: dict[str, JsonValue] = {
        "resource_ref": args.approval_id,
        "requested_action_digest": digest,
    }
    if args.comment is not None:
        if not args.comment.strip():
            raise ProfileError("--comment must not be blank")
        body["comment"] = args.comment
    command = "approval.approve" if args.command == "approve" else "approval.deny"
    return client.post(
        f"/commands/{command}",
        body=body,
        idempotency_key=args.idempotency_key,
    )


def _safe_local_auth_status(
    profile_name: str,
    state: CredentialState | None,
) -> dict[str, JsonValue]:
    if os.getenv("AI_PLATFORM_TOKEN"):
        return {
            "profile": profile_name,
            "authenticated": True,
            "mode": "environment_bearer",
            "credential_store": False,
        }
    if state is None:
        return {"profile": profile_name, "authenticated": False}
    return {
        "profile": profile_name,
        "authenticated": True,
        "mode": state.mode,
        "expires_at": state.expires_at,
        "credential_id": state.credential_id,
    }


def _read_secret(stdin: TextIO, *, from_stdin: bool, prompt: str) -> str:
    if from_stdin:
        value = stdin.readline().rstrip("\r\n")
    else:
        value = getpass.getpass(prompt)
    if not value:
        raise ProfileError("credential input must not be empty")
    return value


def _require_confirmation(
    args: argparse.Namespace,
    description: str,
    *,
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    if bool(args.yes):
        return
    if not stdin.isatty():
        raise ProfileError(f"{description} requires confirmation; re-run with --yes")
    stdout.write(f"Confirm {description}? [y/N] ")
    stdout.flush()
    answer = stdin.readline()
    if answer.strip().casefold() not in {"y", "yes"}:
        raise ProfileError("operation cancelled")


def _add_pagination_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    query = {"limit": str(args.limit), "sort": args.sort, "direction": args.direction}
    if args.cursor:
        query["cursor"] = args.cursor
    if args.q:
        query["q"] = args.q
    if args.fields:
        query["fields"] = args.fields
    for raw_filter in args.filter:
        field, separator, value = raw_filter.partition("=")
        if not separator or not field or not value:
            raise ProfileError("--filter must use FIELD=VALUE")
        query[f"filter[{field}]"] = value
    return query


def _object_body(response: ClientResponse) -> dict[str, JsonValue]:
    if not isinstance(response.body, dict):
        raise TransportError("Control Plane response must be a JSON object")
    return response.body


def _body_string(body: Mapping[str, JsonValue], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TransportError(f"Control Plane response is missing canonical field: {field}")
    return value


def _response_with_body(
    response: ClientResponse,
    body: dict[str, JsonValue],
) -> ClientResponse:
    return ClientResponse(
        status=response.status,
        body=body,
        request_id=response.request_id,
        correlation_id=response.correlation_id,
        api_version=response.api_version,
    )


def _segment(value: str) -> str:
    return quote(value, safe="")


if __name__ == "__main__":
    raise SystemExit(main())
