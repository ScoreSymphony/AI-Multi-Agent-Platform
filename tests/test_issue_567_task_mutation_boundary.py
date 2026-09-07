from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import PlatformKernel, TaskMutationBoundary
from ai_multi_agent_platform.task_management import TaskManagementService
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _kernel() -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
    )


def test_terminal_planning_metadata_uses_supported_boundary_and_preserves_lifecycle() -> None:
    async def scenario() -> None:
        kernel = _kernel()
        management = TaskManagementService(kernel=kernel)
        task = await kernel.create_task(
            idempotency_key="issue-567:create",
            title="Terminal planning metadata",
            objective="Preserve canonical lifecycle terminality",
            owner_type="user",
            owner_id="issue-567-user",
        )
        await kernel.ready_task(
            idempotency_key="issue-567:ready",
            task_id=task.task_id,
        )
        run = await kernel.create_run(
            idempotency_key="issue-567:create-run",
            task_id=task.task_id,
        )
        await kernel.start_run(
            idempotency_key="issue-567:start-run",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        await kernel.record_run_outcome(
            idempotency_key="issue-567:finish-run",
            task_id=task.task_id,
            run_id=run.run_id,
            status=RunStatus.SUCCEEDED,
        )
        terminal = await kernel.get_task(task.task_id)
        assert terminal.status is TaskStatus.SUCCEEDED

        view = await management.update(
            task.task_id,
            {"archived": True},
            idempotency_key="issue-567:archive",
            actor_ref="user:issue-567-user",
        )
        assert view.metadata.archived is True
        assert (await kernel.get_task(task.task_id)).status is TaskStatus.SUCCEEDED

        history = await kernel.history(task.task_id)
        mutation_events = [event for event in history if event.causation_id == "issue-567:archive"]
        assert len(mutation_events) == 1
        event = mutation_events[0]
        assert event.event_type == "task.updated"
        assert event.correlation_id == task.task_id
        assert event.payload["source"] == "task-management"
        metadata = event.payload["metadata"]
        assert isinstance(metadata, Mapping)
        planning = metadata["task_management"]
        assert isinstance(planning, Mapping)
        assert planning["archived"] is True

        replay = await management.update(
            task.task_id,
            {"archived": True},
            idempotency_key="issue-567:archive",
            actor_ref="user:issue-567-user",
        )
        assert replay.metadata.archived is True
        assert len(await kernel.history(task.task_id)) == len(history)

        with pytest.raises(ContractError) as exc_info:
            await management.update(
                task.task_id,
                {"hidden": True},
                idempotency_key="issue-567:archive",
                actor_ref="user:issue-567-user",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert (await kernel.get_task(task.task_id)).status is TaskStatus.SUCCEEDED

        with pytest.raises(ContractError) as invalid_info:
            await management.update(
                task.task_id,
                {"title": "not a planning field"},
                idempotency_key="issue-567:illegal-title",
                actor_ref="user:issue-567-user",
            )
        assert invalid_info.value.code is ErrorCode.INVALID_REQUEST
        assert (await kernel.get_task(task.task_id)).task.title == "Terminal planning metadata"

    asyncio.run(scenario())


def test_project_reassignment_boundary_owns_fixed_event_and_retry_contract() -> None:
    async def scenario() -> None:
        kernel = _kernel()
        mutations = TaskMutationBoundary(kernel)
        source_project_id = new_id("project")
        destination_project_id = new_id("project")
        other_destination_id = new_id("project")
        task = await kernel.create_task(
            idempotency_key="issue-567:project:create",
            title="Project move",
            objective="Use the supported mutation boundary",
            owner_type="user",
            owner_id="issue-567-user",
            project_id=source_project_id,
        )

        moved = await mutations.reassign_project(
            task_id=task.task_id,
            source_project_id=source_project_id,
            destination_project_id=destination_project_id,
            expected_revision=task.revision,
            idempotency_key="issue-567:project:move",
            actor_ref="user:issue-567-user",
        )
        assert moved.task.project_id == destination_project_id
        event = (await kernel.history(task.task_id))[-1]
        assert event.event_type == "task.project_reassigned"
        assert event.project_id == source_project_id
        assert event.payload["source_project_id"] == source_project_id
        assert event.payload["destination_project_id"] == destination_project_id
        assert event.payload["future_execution_scope"] == destination_project_id

        replayed = await mutations.replayed_project_reassignment(
            task_id=task.task_id,
            destination_project_id=destination_project_id,
            idempotency_key="issue-567:project:move",
        )
        assert replayed is not None
        assert replayed.task.project_id == destination_project_id

        with pytest.raises(ContractError) as exc_info:
            await mutations.replayed_project_reassignment(
                task_id=task.task_id,
                destination_project_id=other_destination_id,
                idempotency_key="issue-567:project:move",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_non_kernel_modules_cannot_call_private_task_commit_primitive() -> None:
    package_root = Path(__file__).resolve().parents[1] / "src" / "ai_multi_agent_platform"
    violations: list[str] = []
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        if relative.parts and relative.parts[0] == "kernel":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_commit_task_command"
            ):
                violations.append(f"{relative}:{node.lineno}")
    assert not violations, "private kernel Task commit coupling: " + ", ".join(violations)


def test_task_services_have_no_direct_private_kernel_access() -> None:
    package_root = Path(__file__).resolve().parents[1] / "src" / "ai_multi_agent_platform"
    for relative in (
        Path("task_management/service.py"),
        Path("task_reassignment/service.py"),
    ):
        source = (package_root / relative).read_text(encoding="utf-8")
        assert "self._kernel._" not in source, f"direct private kernel access in {relative}"
