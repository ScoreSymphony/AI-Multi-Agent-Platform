from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.distributed import (
    DistributedLifecycleBackend,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
)
from ai_multi_agent_platform.domain import TaskStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-principal-ref": "user:issue-46",
        "x-owner-type": "user",
        "x-owner-id": "issue-46",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def test_urgent_task_cannot_bypass_distributed_worker_admission() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        lifecycle = DistributedLifecycleBackend(
            DistributedRuntime(DistributedRegistry()),
            requirements=JobRequirements(cpu_cores_min=1.0),
        )
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
        )
        http = ControlPlaneHTTP(ControlPlane(kernel=kernel, events=repository))

        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers("issue-46-w-create"),
                body={
                    "title": "Urgent worker admission fixture",
                    "objective": "Prove priority never bypasses worker eligibility.",
                    "owner_type": "user",
                    "owner_id": "issue-46",
                    "priority": "urgent",
                },
            )
        )
        assert created.status == 201
        assert isinstance(created.body, dict)
        assert created.body["priority"] == "urgent"
        task_id = created.body["id"]
        assert isinstance(task_id, str)

        await kernel.ready_task(
            idempotency_key="issue-46-w-ready",
            task_id=task_id,
            actor_ref="user:issue-46",
        )

        with pytest.raises(ContractError) as rejected:
            await kernel.start_task(
                idempotency_key="issue-46-w-start",
                task_id=task_id,
                actor_ref="user:issue-46",
            )

        assert rejected.value.code is ErrorCode.UNAVAILABLE
        assert rejected.value.retryable is True
        assert rejected.value.provider_id == "distributed-lifecycle"

        canonical_task = await kernel.get_task(task_id)
        assert canonical_task.status is TaskStatus.READY
        projected = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}",
                headers=_headers(),
            )
        )
        assert projected.status == 200
        assert isinstance(projected.body, dict)
        assert projected.body["priority"] == "urgent"
        assert projected.body["status"] == "ready"

        event_types = [event.event_type for event in await kernel.history(task_id)]
        assert "run.created" in event_types
        assert "run.dispatch_attempted" in event_types
        assert "run.running" not in event_types
        assert "task.running" not in event_types

    asyncio.run(scenario())
