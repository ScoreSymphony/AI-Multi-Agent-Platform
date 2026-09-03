from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class ReferenceSearchAuthorization(FakeAuthorizationProvider):
    def __init__(
        self,
        *,
        denied_actions: frozenset[str] = frozenset(),
        denied_project_id: str | None = None,
    ) -> None:
        super().__init__()
        self.denied_actions = denied_actions
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action in self.denied_actions:
            return AuthorizationDecision(allowed=False, reason="reference-hidden")
        if (
            self.denied_project_id is not None
            and request.action.endswith(":list")
            and request.context.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="project-hidden")
        return AuthorizationDecision(allowed=True, reason="reference-visible")


async def _stack(
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP, PlatformKernel, FakeLifecycleBackend]:
    repository = InMemoryKernelRepository()
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
    )
    return control_plane, ControlPlaneHTTP(control_plane), kernel, lifecycle


async def _seed_reference_task(
    kernel: PlatformKernel,
    lifecycle: FakeLifecycleBackend,
    *,
    project_id: str,
    owner_id: str,
    key: str,
    artifact_id: str | None = None,
) -> dict[str, str]:
    task = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title=f"Reference task {key}",
        objective=f"Exercise reference search {key}",
        owner_type="user",
        owner_id=owner_id,
        project_id=project_id,
    )
    await kernel.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id)
    planned = await kernel.plan_task(idempotency_key=f"{key}:plan", task_id=task.task_id)
    run = await kernel.start_task(idempotency_key=f"{key}:start", task_id=task.task_id)
    lifecycle.complete(run.run_id, status=ExecutionStatus.SUCCEEDED, output={"ok": True})
    await kernel.refresh_run(
        idempotency_key=f"{key}:refresh",
        task_id=task.task_id,
        run_id=run.run_id,
    )
    canonical_artifact_id = artifact_id or new_id("artifact")
    result_id = new_id("result")
    await kernel.attach_artifact(
        idempotency_key=f"{key}:artifact",
        task_id=task.task_id,
        run_id=run.run_id,
        artifact_id=canonical_artifact_id,
    )
    await kernel.attach_result(
        idempotency_key=f"{key}:result",
        task_id=task.task_id,
        run_id=run.run_id,
        result_id=result_id,
    )
    assert planned.plan_ref is not None
    assert len(planned.step_ids) == 1
    return {
        "task": task.task_id,
        "run": run.run_id,
        "plan": planned.plan_ref,
        "step": planned.step_ids[0],
        "artifact": canonical_artifact_id,
        "result": result_id,
    }


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(HTTPRequest(method="GET", path="/api/v1/search", query=query))
    assert response.status == 200
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    raw_items = page["items"]
    assert isinstance(raw_items, list)
    assert all(isinstance(item, dict) for item in raw_items)
    return raw_items


def test_plan_step_artifact_and_result_use_global_search() -> None:
    async def scenario() -> None:
        control_plane, http, kernel, lifecycle = await _stack()
        project_id = new_id("project")
        refs = await _seed_reference_task(
            kernel,
            lifecycle,
            project_id=project_id,
            owner_id="reference-owner",
            key="primary",
        )

        expected_paths = {
            "plan": "/api/v1/plans/",
            "step": "/api/v1/steps/",
            "artifact": "/api/v1/artifacts/",
            "result": "/api/v1/results/",
        }
        for resource_type in ("plan", "step", "artifact", "result"):
            page = await _search(http, id=refs[resource_type], type=resource_type)
            assert page["total"] == 1
            item = _items(page)[0]
            assert item["resource_type"] == resource_type
            assert item["resource_id"] == refs[resource_type]
            assert item["project_id"] == project_id
            assert item["owner_type"] == "user"
            assert item["owner_id"] == "reference-owner"
            assert item["canonical_ref"] == expected_paths[resource_type] + refs[resource_type]

        by_task = await _search(
            http,
            q=refs["task"],
            type="plan,step,artifact,result",
        )
        assert by_task["total"] == 4
        assert {item["resource_type"] for item in _items(by_task)} == {
            "plan",
            "step",
            "artifact",
            "result",
        }

        plan_from_step = await _search(http, q=refs["step"], type="plan")
        assert plan_from_step["total"] == 1
        step_from_plan = await _search(http, q=refs["plan"], type="step")
        assert step_from_plan["total"] == 1

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt == 6

    asyncio.run(scenario())


def test_reference_authorization_hides_items_counts_and_exact_lookup() -> None:
    async def scenario() -> None:
        authorization = ReferenceSearchAuthorization(
            denied_actions=frozenset({"plans:list", "artifacts:list"})
        )
        _, http, kernel, lifecycle = await _stack(authorization)
        refs = await _seed_reference_task(
            kernel,
            lifecycle,
            project_id=new_id("project"),
            owner_id="reference-owner",
            key="authorized",
        )

        hidden = await _search(http, type="plan,artifact")
        assert hidden["total"] == 0
        assert _items(hidden) == []
        serialized = repr(hidden)
        assert refs["plan"] not in serialized
        assert refs["artifact"] not in serialized

        hidden_plan = await _search(http, id=refs["plan"], type="plan")
        assert hidden_plan["total"] == 0
        assert refs["plan"] not in repr(hidden_plan)

        hidden_artifact = await _search(http, id=refs["artifact"], type="artifact")
        assert hidden_artifact["total"] == 0
        assert refs["artifact"] not in repr(hidden_artifact)

        visible = await _search(http, type="step,result")
        assert visible["total"] == 2
        assert {item["resource_type"] for item in _items(visible)} == {"step", "result"}

        assert any(call.action == "plans:list" for call in authorization.calls)
        assert any(call.action == "artifacts:list" for call in authorization.calls)
        assert any(call.action == "steps:list" for call in authorization.calls)
        assert any(call.action == "results:list" for call in authorization.calls)

    asyncio.run(scenario())


def test_shared_reference_id_does_not_leak_other_task_relationships() -> None:
    async def scenario() -> None:
        visible_project = new_id("project")
        hidden_project = new_id("project")
        authorization = ReferenceSearchAuthorization(denied_project_id=hidden_project)
        _, http, kernel, lifecycle = await _stack(authorization)
        shared_artifact = new_id("artifact")

        visible = await _seed_reference_task(
            kernel,
            lifecycle,
            project_id=visible_project,
            owner_id="visible-owner",
            key="visible",
            artifact_id=shared_artifact,
        )
        hidden = await _seed_reference_task(
            kernel,
            lifecycle,
            project_id=hidden_project,
            owner_id="hidden-owner",
            key="hidden",
            artifact_id=shared_artifact,
        )

        page = await _search(http, id=shared_artifact, type="artifact")
        assert page["total"] == 1
        artifact = _items(page)[0]
        assert artifact["resource_id"] == shared_artifact
        assert artifact["project_id"] is None
        assert artifact["owner_type"] is None
        assert artifact["owner_id"] is None
        serialized = repr(page)
        assert visible["task"] not in serialized
        assert hidden["task"] not in serialized
        assert visible_project not in serialized
        assert hidden_project not in serialized
        assert "visible-owner" not in serialized
        assert "hidden-owner" not in serialized

        hidden_relationship = await _search(http, q=hidden["task"], type="artifact")
        assert hidden_relationship["total"] == 0
        visible_relationship = await _search(http, q=visible["task"], type="artifact")
        assert visible_relationship["total"] == 0

    asyncio.run(scenario())
