from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.credentials import CredentialStore
from ai_multi_agent_platform.cli.issue_214 import run_cli
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore
from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
)
from ai_multi_agent_platform.domain import ApprovalStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorType,
    ApprovalService,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    ScryptPasswordHasher,
    infer_actor_identity,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

PASSWORD = "correct horse battery staple"


class RecordingTransport:
    def __init__(self, http: Any) -> None:
        self.http = http
        self.calls: list[tuple[str, str, dict[str, str], dict[str, str], dict[str, Any]]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del timeout
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query))
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        normalized_headers = {key.casefold(): value for key, value in headers.items()}
        self.calls.append((method, parsed.path, query, normalized_headers, decoded))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=query,
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _kernel() -> tuple[PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return kernel, repository


def _profile(config: Path, *, principal_ref: str | None = None) -> None:
    store = ProfileStore.load(config)
    store.set_profile(
        "local",
        CLIProfile(
            endpoint="http://control.local",
            principal_ref=principal_ref,
            owner_type="user" if principal_ref else None,
            owner_id=principal_ref.removeprefix("user:") if principal_ref else None,
        ),
    )
    store.use("local")
    store.save()


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
    stdin: StringIO | None = None,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
    )
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else {}
    assert isinstance(output, dict)
    assert isinstance(error, dict)
    return code, output, error


def _authentication_fixture(
    config: Path,
) -> tuple[RecordingTransport, LocalAuthenticationService, str]:
    auth = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    user = auth.bootstrap_first_admin("alice", PASSWORD)
    kernel, repository = _kernel()
    control_plane = ControlPlane(kernel=kernel, events=repository)
    http = AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False)
    _profile(config)
    return RecordingTransport(http), auth, user.user_id


def _login(config: Path, transport: RecordingTransport) -> dict[str, Any]:
    code, output, error = _invoke(
        config,
        transport,
        "auth",
        "login",
        "--username",
        "alice",
        "--password-stdin",
        stdin=StringIO(PASSWORD + "\n"),
    )
    assert code == 0 and not error
    return output


def _pending_gate(
    *,
    lifetime: timedelta = timedelta(minutes=15),
) -> tuple[AuthorizationGate, ProposedAction, str]:
    requester = LocalPrincipalPolicy(
        principal_ref="user:requester",
        actor_types=frozenset({ActorType.HUMAN}),
        approval_actions=frozenset({AuthorizationAction.EXECUTE}),
        resource_types=frozenset({ResourceType.TASK}),
    )
    approver = LocalPrincipalPolicy(
        principal_ref="user:approver",
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset({AuthorizationAction.APPROVE}),
        resource_types=frozenset({ResourceType.TASK}),
    )
    gate = AuthorizationGate(
        LocalAuthorizationProvider((requester, approver)),
        approvals=ApprovalService(default_lifetime=lifetime),
    )
    task_id = new_id("task")
    action = ProposedAction(
        AuthorizationContext(
            actor=infer_actor_identity("user:requester"),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            operation=OperationContext(
                correlation_id="corr-issue-214",
                owner_type="user",
                owner_id="requester",
            ),
            task_id=task_id,
            side_effect="issue-214-sensitive-action",
        ),
        payload={
            "operation": "sensitive-test",
            "secret_token": "do-not-expose-approval-payload",
        },
    )
    decision = asyncio.run(gate.decide(action))
    assert decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    approval_id = cast(str, decision.constraints["approval_id"])
    return gate, action, approval_id


def _approval_transport(gate: AuthorizationGate) -> RecordingTransport:
    kernel, repository = _kernel()
    control_plane = ControlPlane(kernel=kernel, events=repository, approval_gate=gate)
    return RecordingTransport(ControlPlaneHTTP(control_plane))


def test_auth_cli_login_me_logout_keeps_secrets_out_of_profile_and_output(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport, _, user_id = _authentication_fixture(config)

    login = _login(config, transport)
    serialized_login = json.dumps(login, sort_keys=True)
    assert PASSWORD not in serialized_login
    assert "csrf_token" not in serialized_login
    assert "amp_session" not in serialized_login
    assert login["data"]["authenticated"] is True

    profile_text = config.read_text(encoding="utf-8")
    assert PASSWORD not in profile_text
    assert "amp_session" not in profile_text
    assert "csrf_token" not in profile_text

    credentials = CredentialStore.load(config)
    state = credentials.get("local")
    assert state is not None and state.mode == "session"
    assert state.session_cookie is not None and state.session_cookie.startswith("amp_session=")
    assert state.csrf_token is not None

    code, me, error = _invoke(config, transport, "auth", "me")
    assert code == 0 and not error
    assert me["data"]["actor_id"] == user_id
    me_call = transport.calls[-1]
    assert me_call[0:2] == ("GET", "/api/v1/auth/me")
    assert me_call[3]["cookie"].startswith("amp_session=")
    assert "x-csrf-token" not in me_call[3]

    code, logout, error = _invoke(config, transport, "auth", "logout")
    assert code == 0 and not error
    assert logout["data"]["logged_out"] is True
    logout_call = transport.calls[-1]
    assert logout_call[0:2] == ("POST", "/api/v1/auth/logout")
    assert logout_call[3]["x-csrf-token"] == state.csrf_token
    assert CredentialStore.load(config).get("local") is None


def test_auth_cli_reports_unauthenticated_revoked_and_expired_credentials(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport, auth, user_id = _authentication_fixture(config)

    code, output, error = _invoke(config, transport, "auth", "me")
    assert code == 3 and not output
    assert error["status"] == 401
    assert error["category"] == "authentication"

    _login(config, transport)
    session_id = next(iter(auth.store.sessions))
    auth.revoke_session(user_id, session_id)
    code, output, error = _invoke(config, transport, "auth", "me")
    assert code == 3 and not output
    assert error["status"] == 401
    assert error["category"] == "authentication"

    CredentialStore.load(config).clear("local")
    _login(config, transport)
    session_id = next(
        session_id
        for session_id, session in auth.store.sessions.items()
        if session.revoked_at is None
    )
    session = auth.store.sessions[session_id]
    auth.store.sessions[session_id] = replace(
        session,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    code, output, error = _invoke(config, transport, "auth", "me")
    assert code == 3 and not output
    assert error["status"] == 401
    assert error["category"] == "authentication"


def test_approval_cli_approves_exact_action_idempotently_without_payload_leak(
    tmp_path: Path,
) -> None:
    gate, action, approval_id = _pending_gate()
    transport = _approval_transport(gate)
    config = tmp_path / "cli.json"
    _profile(config, principal_ref="user:approver")

    arguments = (
        "--yes",
        "approval",
        "approve",
        approval_id,
        "--comment",
        "reviewed",
        "--idempotency-key",
        "approval-decision-1",
    )
    code, output, error = _invoke(config, transport, *arguments)
    assert code == 0 and not error
    assert output["data"]["status"] == "approved"
    assert output["data"]["requested_action_digest"] == action.digest
    assert output["data"]["decision_by"] == {"type": "user", "id": "approver"}

    serialized = json.dumps(output, sort_keys=True)
    assert "do-not-expose-approval-payload" not in serialized
    assert "secret_token" not in serialized

    post = transport.calls[-1]
    assert post[0:2] == ("POST", "/api/v1/commands/approval.approve")
    assert post[3]["idempotency-key"] == "approval-decision-1"
    assert post[4]["resource_ref"] == approval_id
    assert post[4]["requested_action_digest"] == action.digest

    code, repeated, error = _invoke(config, transport, *arguments)
    assert code == 2
    assert repeated == {}
    assert "approval is not pending: approved" in error["message"]

    # The canonical command itself remains idempotent even after the CLI preflight sees
    # the terminal state, which protects transport retries between POST and response.
    client_call = transport.calls[-2]
    assert client_call[0:2] == ("GET", f"/api/v1/approvals/{approval_id}")


def test_approval_contract_rejects_wrong_digest_unauthorized_actor_and_nonpending(
    tmp_path: Path,
) -> None:
    gate, action, approval_id = _pending_gate()
    transport = _approval_transport(gate)
    config = tmp_path / "cli.json"
    _profile(config, principal_ref="user:approver")

    wrong_body = {
        "resource_ref": approval_id,
        "requested_action_digest": "0" * 64,
    }
    response = transport.request(
        "POST",
        "http://control.local/api/v1/commands/approval.approve",
        headers={
            "content-type": "application/json",
            "x-principal-ref": "user:approver",
            "x-owner-type": "user",
            "x-owner-id": "approver",
            "idempotency-key": "wrong-digest",
        },
        body=json.dumps(wrong_body).encode("utf-8"),
        timeout=1.0,
    )
    assert response.status == 409
    assert gate.approvals.get(approval_id).status is ApprovalStatus.PENDING

    _profile(config, principal_ref="user:unauthorized")
    code, output, error = _invoke(
        config,
        transport,
        "--yes",
        "approval",
        "approve",
        approval_id,
        "--idempotency-key",
        "unauthorized-decision",
    )
    assert code == 3 and not output
    assert error["status"] == 403
    assert error["category"] == "authorization"
    assert gate.approvals.get(approval_id).status is ApprovalStatus.PENDING

    _profile(config, principal_ref="user:approver")
    code, output, error = _invoke(
        config,
        transport,
        "--yes",
        "approval",
        "approve",
        approval_id,
        "--idempotency-key",
        "authorized-decision",
    )
    assert code == 0 and not error
    assert output["data"]["requested_action_digest"] == action.digest

    terminal_body = {
        "resource_ref": approval_id,
        "requested_action_digest": action.digest,
    }
    response = transport.request(
        "POST",
        "http://control.local/api/v1/commands/approval.approve",
        headers={
            "content-type": "application/json",
            "x-principal-ref": "user:approver",
            "x-owner-type": "user",
            "x-owner-id": "approver",
            "idempotency-key": "second-decision",
        },
        body=json.dumps(terminal_body).encode("utf-8"),
        timeout=1.0,
    )
    assert response.status == 409


def test_approval_cli_deny_and_expired_conflict_paths(tmp_path: Path) -> None:
    gate, _, approval_id = _pending_gate()
    transport = _approval_transport(gate)
    config = tmp_path / "cli.json"
    _profile(config, principal_ref="user:approver")

    code, output, error = _invoke(
        config,
        transport,
        "--yes",
        "approval",
        "deny",
        approval_id,
        "--idempotency-key",
        "deny-decision",
    )
    assert code == 0 and not error
    assert output["data"]["status"] == "rejected"

    expiring_gate, _, expiring_id = _pending_gate(lifetime=timedelta(microseconds=1))
    expiring_transport = _approval_transport(expiring_gate)
    expired = expiring_gate.approvals.get(expiring_id)
    if expired.status is ApprovalStatus.PENDING:
        expiring_gate.approvals._records[expiring_id] = replace(
            expired,
            expires_at=datetime.now(UTC) - timedelta(microseconds=1),
        )
    code, output, error = _invoke(
        config,
        expiring_transport,
        "--yes",
        "approval",
        "approve",
        expiring_id,
        "--idempotency-key",
        "expired-decision",
    )
    assert code == 2 and not output
    assert "approval is not pending: expired" in error["message"]
