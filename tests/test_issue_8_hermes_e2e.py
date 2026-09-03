from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_ADAPTER_ID,
    HermesAdapterConfig,
    HermesHttpResponse,
    HermesOrchestrator,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel, TaskStatus
from ai_multi_agent_platform.orchestration import OrchestratorRegistry, OrchestratorSelection
from ai_multi_agent_platform.testing import FakeOrchestrator


@dataclass(frozen=True, slots=True)
class E2ERequest:
    method: str
    url: str
    payload: Mapping[str, JsonValue] | None


class E2EHermesTransport:
    def __init__(self) -> None:
        self.calls: list[E2ERequest] = []
        self.status_reads = 0

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        del headers, timeout_seconds
        self.calls.append(E2ERequest(method, url, payload))
        if method == "POST" and url.endswith("/v1/runs"):
            return HermesHttpResponse(
                202,
                {"run_id": "run_hermes_e2e", "status": "started"},
            )
        if method == "GET" and url.endswith("/v1/runs/run_hermes_e2e"):
            self.status_reads += 1
            return HermesHttpResponse(
                200,
                {
                    "run_id": "run_hermes_e2e",
                    "status": "completed",
                    "output": json.dumps(
                        {
                            "summary": "Hermes proposed the execution plan",
                            "steps": [
                                {
                                    "key": "execute",
                                    "title": "Execute through reference backend",
                                    "objective": "Use the non-Hermes executor",
                                    "depends_on": [],
                                }
                            ],
                        }
                    ),
                },
            )
        raise AssertionError(f"unexpected Hermes request: {method} {url}")


def test_kernel_uses_hermes_for_planning_and_reference_executor_for_execution(
    tmp_path: Path,
) -> None:
    task_id = new_id("task")
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / task_id
    workspace.mkdir(parents=True)
    transport = E2EHermesTransport()
    hermes = HermesOrchestrator(
        HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
        transport=transport,
        secret_resolver=lambda _: None,
    )
    lifecycle = ExecutorLifecycleBackend(
        ReferenceExecutor(workspace_root),
        workspace=task_id,
        action="write_artifact",
    )
    kernel = PlatformKernel(orchestrator=hermes, lifecycle=lifecycle)

    async def scenario() -> None:
        await kernel.create_task(
            idempotency_key="hermes-e2e:create",
            task_id=task_id,
            title="Hermes E2E",
            objective="Plan with Hermes and execute without Hermes",
            owner_type="user",
            owner_id="issue-8-test",
        )
        await kernel.ready_task(
            idempotency_key="hermes-e2e:ready",
            task_id=task_id,
        )
        run = await kernel.start_task(
            idempotency_key="hermes-e2e:start",
            task_id=task_id,
        )
        run = await kernel.refresh_run(
            idempotency_key="hermes-e2e:refresh",
            task_id=task_id,
            run_id=run.run_id,
        )
        task = await kernel.get_task(task_id)
        history = await kernel.history(task_id)

        assert run.status.value == "succeeded"
        assert task.status is TaskStatus.SUCCEEDED
        assert (workspace / "artifact.txt").exists()
        assert any(event.event_type == "plan.created" for event in history)
        plan_event = next(event for event in history if event.event_type == "plan.created")
        assert any(
            item.namespace == "hermes" and item.values["external_run_id"] == "run_hermes_e2e"
            for item in plan_event.adapter_metadata
        )
        assert run.run.id.startswith("run_")
        assert run.run.id != "run_hermes_e2e"
        assert transport.status_reads == 1

    asyncio.run(scenario())


def test_orchestrator_selection_is_configuration_driven_and_fail_closed() -> None:
    reference = FakeOrchestrator()
    hermes = HermesOrchestrator(
        HermesAdapterConfig(enabled=True),
        transport=E2EHermesTransport(),
        secret_resolver=lambda _: None,
    )
    registry = OrchestratorRegistry(
        {
            "reference": reference,
            HERMES_ADAPTER_ID: hermes,
        }
    )

    assert registry.select(OrchestratorSelection("reference")) is reference
    assert registry.select(OrchestratorSelection(HERMES_ADAPTER_ID)) is hermes
    assert registry.orchestrator_ids == (HERMES_ADAPTER_ID, "reference")

    with pytest.raises(ContractError) as unknown_error:
        registry.select(OrchestratorSelection("unknown"))
    assert unknown_error.value.code is ErrorCode.INVALID_CONFIGURATION

    disabled = OrchestratorRegistry(
        {
            HERMES_ADAPTER_ID: HermesOrchestrator(HermesAdapterConfig(enabled=False)),
        }
    )
    with pytest.raises(ContractError) as disabled_error:
        disabled.select(OrchestratorSelection(HERMES_ADAPTER_ID))
    assert disabled_error.value.code is ErrorCode.UNAVAILABLE
