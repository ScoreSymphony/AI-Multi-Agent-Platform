from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def test_coordination_repair_commands_are_authorized_before_handler_execution() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        authorization = FakeAuthorizationProvider(allowed=False)
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=repository,
            ),
            events=repository,
            authorization=authorization,
        )
        executed: list[str] = []

        async def handler(context: RequestContext, resource_ref: str, payload: dict) -> dict:
            del context, payload
            executed.append(resource_ref)
            return {"id": resource_ref}

        control_plane.register_command("coordination.reconcile", handler)
        control_plane.register_command("coordination.cancel", handler)
        control_plane.register_command("coordination.repair", handler)
        context = RequestContext(
            request_id="issue-384-authorization-request",
            correlation_id="issue-384-authorization-correlation",
            actor=ActorContext(principal_ref="issue-384-operator"),
            idempotency_key="issue-384-command-key",
        )

        for command in (
            "coordination.reconcile",
            "coordination.cancel",
            "coordination.repair",
        ):
            with pytest.raises(ContractError) as caught:
                await control_plane.execute_command(
                    context,
                    command,
                    "plan_issue384",
                )
            assert caught.value.code is ErrorCode.FORBIDDEN

        assert executed == []
        assert [call.action for call in authorization.calls] == [
            "coordination.reconcile",
            "coordination.cancel",
            "coordination.repair",
        ]

    asyncio.run(scenario())


def test_coordination_mutations_require_idempotency_key_before_authorization() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        authorization = FakeAuthorizationProvider(allowed=True)
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=repository,
            ),
            events=repository,
            authorization=authorization,
        )

        async def handler(context: RequestContext, resource_ref: str, payload: dict) -> dict:
            del context, payload
            return {"id": resource_ref}

        control_plane.register_command("coordination.reconcile", handler)
        context = RequestContext(
            request_id="issue-384-idempotency-request",
            correlation_id="issue-384-idempotency-correlation",
            actor=ActorContext(principal_ref="issue-384-operator"),
        )

        with pytest.raises(ContractError) as caught:
            await control_plane.execute_command(
                context,
                "coordination.reconcile",
                "plan_issue384",
            )
        assert caught.value.code is ErrorCode.INVALID_REQUEST
        assert authorization.calls == []

    asyncio.run(scenario())
