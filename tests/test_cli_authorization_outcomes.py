from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import ApprovalStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str]] = []

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
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append((method, parsed.path))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "current_profile": "secured",
                "profiles": {
                    "secured": {
                        "endpoint": "http://control-plane.test",
                        "principal_ref": "user:test",
                        "owner_type": "user",
                        "owner_id": "test",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _kernel() -> tuple[InMemoryKernelRepository, PlatformKernel]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return repository, kernel


def _create_task(kernel: PlatformKernel) -> str:
    task = asyncio.run(
        kernel.create_task(
            idempotency_key="cli-authorization-task",
            title="Authorization fixture",
            objective="Prove CLI authorization outcomes",
            owner_type="user",
            owner_id="test",
            actor_ref="user:test",
        )
    )
    return task.task_id


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    success = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else {}
    assert isinstance(success, dict)
    assert isinstance(error, dict)
    return code, success, error


def test_cli_surfaces_canonical_authorization_denial(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _config(config)
    repository, kernel = _kernel()
    task_id = _create_task(kernel)
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:test",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.CREATE}),
                resource_types=frozenset({ResourceType.TASK}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    transport = RecordingTransport(
        ControlPlaneHTTP(
            ControlPlane(
                kernel=kernel,
                events=repository,
                authorization=ControlPlaneAuthorizationBridge(gate),
            )
        )
    )

    code, success, error = _invoke(config, transport, "task", "show", task_id)

    assert code == 3
    assert success == {}
    assert error["code"] == "forbidden"
    assert error["status"] == 403
    assert error["message"] == "action is not granted by local policy"
    assert error["details"]["authorization_outcome"] == "deny"
    assert error["details"]["policy_id"] == "local:user:test"
    assert isinstance(error["request_id"], str)
    assert isinstance(error["correlation_id"], str)
    assert gate.approvals.all() == ()
    assert transport.calls == [("GET", f"/api/v1/tasks/{task_id}")]


def test_cli_surfaces_approval_required_and_observes_approved_action(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _config(config)
    repository, kernel = _kernel()
    task_id = _create_task(kernel)
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:test",
                actor_types=frozenset({ActorType.HUMAN}),
                approval_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.TASK}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    transport = RecordingTransport(
        ControlPlaneHTTP(
            ControlPlane(
                kernel=kernel,
                events=repository,
                authorization=ControlPlaneAuthorizationBridge(gate),
            )
        )
    )

    code, success, error = _invoke(config, transport, "task", "show", task_id)

    assert code == 3
    assert success == {}
    assert error["code"] == "forbidden"
    assert error["status"] == 403
    assert error["message"] == "action requires approval by local policy"
    assert error["details"]["authorization_outcome"] == "require_approval"
    approval_id = error["details"]["approval_id"]
    requested_digest = error["details"]["requested_action_digest"]
    assert isinstance(approval_id, str)
    assert isinstance(requested_digest, str)

    pending = gate.approvals.get(approval_id)
    assert pending.status is ApprovalStatus.PENDING
    assert pending.resource_id == task_id
    assert pending.requested_action_digest == requested_digest

    asyncio.run(
        gate.decide_approval(
            approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="cli-approval-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )
    assert gate.approvals.get(approval_id).status is ApprovalStatus.APPROVED

    retry_code, retry_success, retry_error = _invoke(
        config,
        transport,
        "task",
        "show",
        task_id,
    )

    assert retry_code == 0
    assert retry_error == {}
    assert retry_success["data"]["id"] == task_id
    assert retry_success["data"]["status"] == "draft"
    assert transport.calls == [
        ("GET", f"/api/v1/tasks/{task_id}"),
        ("GET", f"/api/v1/tasks/{task_id}"),
    ]
