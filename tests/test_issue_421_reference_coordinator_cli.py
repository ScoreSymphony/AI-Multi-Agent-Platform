from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    coordination_resource_services,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Run, RunStatus, Step, Task, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository
from ai_multi_agent_platform.kernel.models import RunState, TaskState


class ReferenceCoordinatorKernel:
    """Deterministic canonical Run kernel used by the real #384 coordinator."""

    def __init__(self, *, plan: Plan, steps: tuple[Step, ...]) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="Reference coordinator client fixture",
                owner_ref=plan.owner_ref,
                project_id=plan.project_id,
                status=TaskStatus.READY,
            ),
            revision=1,
            plan_ref=plan.id,
            step_ids=tuple(step.id for step in steps),
        )
        self.runs: dict[str, RunState] = {}
        self.by_key: dict[str, str] = {}

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.task.task_id
        return self.task

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        assert task_id == self.task.task_id
        return self.runs[run_id]

    async def create_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        subject_type: str = "task",
        subject_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del actor_ref, source
        assert task_id == self.task.task_id
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return self.runs[existing]
        assert subject_type == "step"
        assert subject_id is not None
        run = Run(
            subject_type="step",
            subject_id=subject_id,
            owner_ref=self.task.task.owner_ref,
            correlation_id=task_id,
            attempt=1,
            project_id=self.task.task.project_id,
        )
        state = RunState(run=run, revision=1)
        self.runs[run.id] = state
        self.by_key[idempotency_key] = run.id
        return state

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del idempotency_key, actor_ref, source
        current = await self.get_run(task_id, run_id)
        running = replace(
            current,
            run=replace(current.run, status=RunStatus.RUNNING),
            revision=current.revision + 1,
        )
        self.runs[run_id] = running
        if self.task.status is TaskStatus.READY:
            self.task = replace(
                self.task,
                task=replace(self.task.task, status=TaskStatus.RUNNING),
                revision=self.task.revision + 1,
                run_ids=(*self.task.run_ids, run_id),
            )
        return running


class RecordingControlPlaneTransport:
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


def _write_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "current_profile": "reference",
                "profiles": {
                    "reference": {
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


def test_cli_reads_real_reference_coordinator_projection_through_control_plane(tmp_path: Path) -> None:
    owner = OwnerRef(type="user", id="test")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    root = Step(
        plan_id=plan.id,
        title="root",
        owner_ref=owner,
        project_id=plan.project_id,
    )
    dependent = Step(
        plan_id=plan.id,
        title="dependent",
        owner_ref=owner,
        project_id=plan.project_id,
        depends_on=(root.id,),
    )
    steps = (root, dependent)
    kernel = ReferenceCoordinatorKernel(plan=plan, steps=steps)
    coordinator = DurablePlanStepCoordinator(
        repository=InMemoryCoordinatorRepository(),
        kernel=kernel,
        coordinator_id="reference-coordinator",
    )
    asyncio.run(coordinator.register_plan(plan, steps))

    events = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=kernel,  # type: ignore[arg-type]
        events=events,
        resource_services=coordination_resource_services(coordinator),
    )
    transport = RecordingControlPlaneTransport(ControlPlaneHTTP(control_plane))
    config = tmp_path / "cli.json"
    _write_config(config)
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "--json",
            "task",
            "workflow",
            "steps",
            plan.task_id,
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    assert transport.calls == [
        ("GET", f"/api/v1/tasks/{plan.task_id}"),
        ("GET", f"/api/v1/plan-coordination/{plan.id}"),
    ]
    payload = json.loads(stdout.getvalue())
    data = payload["data"]
    assert data["task_id"] == plan.task_id
    assert data["plan_id"] == plan.id
    assert data["plan_revision"] == plan.revision
    assert data["total"] == 2

    by_id = {item["id"]: item for item in data["items"]}
    assert by_id[root.id]["status"] == "running"
    assert by_id[root.id]["coordination_phase"] == "attempt_active"
    assert by_id[root.id]["latest_run_id"] in kernel.runs
    assert by_id[dependent.id]["status"] == "pending"
    assert by_id[dependent.id]["coordination_phase"] == "blocked"
    assert by_id[dependent.id]["dependency_ids"] == [root.id]
    assert by_id[dependent.id]["satisfied_dependency_ids"] == []
    assert all(
        "lease_token" not in item
        and "backend_workflow_id" not in item
        and "coordinator_owner_token" not in item
        for item in data["items"]
    )
