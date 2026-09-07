from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security.approval_control_plane import approval_resource_services
from ai_multi_agent_platform.security.authorization import (
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    infer_actor_identity,
)
from ai_multi_agent_platform.security.enforcement import AuthorizationGate
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str, dict[str, str]]] = []

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
        self.calls.append((method, parsed.path, query))
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


def _http(*, gate: AuthorizationGate | None = None) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=None if gate is None else approval_resource_services(gate.approvals),
    )
    return ControlPlaneHTTP(control_plane)


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def _pending_gate() -> tuple[AuthorizationGate, ProposedAction, str]:
    policy = LocalPrincipalPolicy(
        principal_ref="user:requester",
        actor_types=frozenset({ActorType.HUMAN}),
        approval_actions=frozenset({AuthorizationAction.EXECUTE}),
        resource_types=frozenset({ResourceType.TASK}),
    )
    gate = AuthorizationGate(LocalAuthorizationProvider((policy,)))
    task_id = new_id("task")
    action = ProposedAction(
        AuthorizationContext(
            actor=infer_actor_identity("user:requester"),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            operation=OperationContext(
                correlation_id="corr-cli-approval",
                owner_type="user",
                owner_id="requester",
            ),
            task_id=task_id,
            side_effect="test-sensitive-action",
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


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return cast(list[dict[str, Any]], items)


def test_cli_lists_and_shows_pending_approval_without_payload_leak(tmp_path: Path) -> None:
    gate, action, approval_id = _pending_gate()
    transport = RecordingTransport(_http(gate=gate))
    config = tmp_path / "cli.json"

    code, page, error = _invoke(
        config,
        transport,
        "extension",
        "list",
        "approvals",
        "--filter",
        "status=pending",
    )
    assert code == 0 and not error
    items = _items(page)
    assert len(items) == 1
    assert items[0]["id"] == approval_id
    assert items[0]["status"] == "pending"

    code, shown, error = _invoke(
        config,
        transport,
        "extension",
        "show",
        "approvals",
        approval_id,
    )
    assert code == 0 and not error
    approval = shown["data"]
    assert isinstance(approval, dict)
    assert approval["action"] == "execute"
    assert approval["resource_type"] == "task"
    assert approval["resource_id"] == action.context.resource_id
    assert approval["requester_ref"] == "user:requester"
    assert approval["requested_action_digest"] == action.digest
    assert approval["decision_by"] is None
    assert approval["decision_at"] is None

    serialized = json.dumps({"page": page, "shown": shown}, sort_keys=True)
    assert "do-not-expose-approval-payload" not in serialized
    assert "secret_token" not in serialized

    assert transport.calls == [
        ("GET", "/api/v1/openapi.json", {}),
        (
            "GET",
            "/api/v1/approvals",
            {
                "limit": "50",
                "sort": "id",
                "direction": "asc",
                "filter[status]": "pending",
            },
        ),
        ("GET", "/api/v1/openapi.json", {}),
        ("GET", f"/api/v1/approvals/{approval_id}", {}),
    ]


def test_cli_has_no_approval_backend_fallback_when_collection_is_absent(tmp_path: Path) -> None:
    transport = RecordingTransport(_http())
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(
        config,
        transport,
        "extension",
        "list",
        "approvals",
    )

    assert code == 2
    assert payload == {}
    assert "canonical extension collection is not registered: approvals" in error
    assert transport.calls == [("GET", "/api/v1/openapi.json", {})]
