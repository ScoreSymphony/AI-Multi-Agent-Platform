from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.terminal import ReferenceTerminalAdapter, TerminalSessionService
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class SelectiveRunCancelAuthorization(AuthorizationProvider):
    def __init__(self) -> None:
        self.allow_run_cancel = False

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="terminal-run-cancel-policy",
            provider_type="authorization",
            health=HealthStatus.HEALTHY,
        )

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        if request.action == "run:cancel" and not self.allow_run_cancel:
            return AuthorizationDecision(False, reason="run cancellation denied by policy")
        return AuthorizationDecision(True, reason="terminal hardening test allows action")


def test_terminal_termination_cannot_bypass_run_cancel_authorization() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        principal = "user:terminal-run-policy"
        terminal_policy = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=principal,
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset(
                        {
                            AuthorizationAction.READ,
                            AuthorizationAction.CREATE,
                            AuthorizationAction.EXECUTE,
                            AuthorizationAction.MODIFY,
                        }
                    ),
                    resource_types=frozenset({ResourceType.GENERIC}),
                    project_ids=frozenset({project_id}),
                    workspace_ids=frozenset({workspace_id}),
                ),
            )
        )
        terminal = TerminalSessionService(
            AuthorizationGate(terminal_policy),
            (ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
        )
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        northbound_policy = SelectiveRunCancelAuthorization()
        http = ControlPlaneHTTP(
            ControlPlane(
                kernel=kernel,
                events=repository,
                authorization=northbound_policy,
                terminal_sessions=terminal,
            )
        )
        headers = {
            "Content-Type": "application/json",
            "X-Request-Id": "request-terminal-run-policy",
            "X-Correlation-Id": "corr-terminal-run-policy",
            "X-Principal-Ref": principal,
            "X-Owner-Type": "user",
            "X-Owner-Id": "terminal-run-policy",
        }

        task = await kernel.create_task(
            idempotency_key="run-policy-task-create",
            title="Protected run",
            objective="Verify run cancellation authorization",
            owner_type="user",
            owner_id="terminal-run-policy",
            project_id=project_id,
            actor_ref=principal,
        )
        await kernel.ready_task(
            idempotency_key="run-policy-task-ready",
            task_id=task.task.id,
            actor_ref=principal,
        )
        run = await kernel.start_task(
            idempotency_key="run-policy-task-start",
            task_id=task.task.id,
            actor_ref=principal,
        )
        assert (await kernel.get_run(task.task.id, run.run_id)).status is RunStatus.RUNNING

        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.create",
                headers={**headers, "Idempotency-Key": "run-policy-terminal-create"},
                body={
                    "resource_ref": project_id,
                    "workspace_id": workspace_id,
                    "session_type": "process",
                    "mode": "read_only",
                    "task_id": task.task.id,
                    "run_id": run.run_id,
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        session_id = created.body["id"]
        assert isinstance(session_id, str)

        denied = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.terminate",
                headers={**headers, "Idempotency-Key": "run-policy-terminal-terminate"},
                body={"resource_ref": session_id, "reason": "operator request"},
            )
        )
        assert denied.status == 403
        assert (await kernel.get_run(task.task.id, run.run_id)).status is RunStatus.RUNNING

        terminal_state = await terminal.get_session(
            session_id,
            actor_ref=principal,
            operation=OperationContext(
                correlation_id="terminal-state-check",
                project_id=project_id,
            ),
        )
        assert terminal_state.status.value == "cancelled"

        northbound_policy.allow_run_cancel = True
        retried = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/terminal.session.terminate",
                headers={**headers, "Idempotency-Key": "run-policy-terminal-terminate"},
                body={"resource_ref": session_id, "reason": "operator request"},
            )
        )
        assert retried.status == 200
        assert (await kernel.get_run(task.task.id, run.run_id)).status is RunStatus.CANCELLED

    asyncio.run(scenario())
