from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security.approval_control_plane import approval_resource_services
from ai_multi_agent_platform.security.approvals import ApprovalRecord, ApprovalService
from ai_multi_agent_platform.security.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class ApprovalSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str | None = None) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if (
            self.denied_project_id is not None
            and request.action == "approval:list"
            and request.context.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="approval-project-hidden")
        return AuthorizationDecision(allowed=True, reason="approval-search-visible")


async def _stack(
    approvals: ApprovalService,
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        resource_services=approval_resource_services(approvals),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def _seed_approval(
    approvals: ApprovalService,
    *,
    project_id: str,
    owner_id: str,
    secret: str,
    reason: str,
) -> tuple[ApprovalRecord, ProposedAction, str]:
    task_id = new_id("task")
    action = ProposedAction(
        AuthorizationContext(
            actor=infer_actor_identity(f"user:{owner_id}"),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=task_id,
            operation=OperationContext(
                correlation_id=f"approval-search-{owner_id}",
                owner_type="user",
                owner_id=owner_id,
                project_id=project_id,
            ),
            task_id=task_id,
            capability_ref="capability:approval-search",
            side_effect="approval-search-sensitive-action",
        ),
        payload={
            "operation": "sensitive-approval-search-test",
            "secret_token": secret,
        },
    )
    record = approvals.request(
        action,
        reason=reason,
        policy_id="policy.approval-search",
        risk=RiskClassification.HIGH,
    )
    return record, action, task_id


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


def test_approval_uses_registered_global_search_without_sensitive_payload_text() -> None:
    async def scenario() -> None:
        approvals = ApprovalService()
        control_plane, http = await _stack(approvals)
        project_id = new_id("project")
        secret = "do-not-expose-approval-payload"
        reason = "Sensitive human approval rationale must stay out of global search"
        record, action, task_id = _seed_approval(
            approvals,
            project_id=project_id,
            owner_id="approval-owner",
            secret=secret,
            reason=reason,
        )

        page = await _search(http, id=record.approval_id, type="approval")
        assert page["total"] == 1
        approval = _items(page)[0]
        assert approval["resource_type"] == "approval"
        assert approval["resource_id"] == record.approval_id
        assert approval["title"] == f"Approval for task {task_id}"
        assert approval["summary"] == ""
        assert approval["project_id"] == project_id
        assert approval["owner_type"] == "user"
        assert approval["owner_id"] == "approval-owner"
        assert approval["status"] == "pending"
        assert approval["canonical_ref"] == f"/api/v1/approvals/{record.approval_id}"
        assert approval["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": "approvals",
        }

        by_action = await _search(http, q="execute", type="approval")
        assert by_action["total"] == 1
        by_risk = await _search(http, q="high", type="approval")
        assert by_risk["total"] == 1
        by_task = await _search(http, q=task_id, type="approval")
        assert by_task["total"] == 1
        by_project = await _search(http, project_id=project_id, type="approval")
        assert by_project["total"] == 1
        pending = await _search(http, status="pending", type="approval")
        assert pending["total"] == 1

        secret_query = await _search(http, q=secret, type="approval")
        assert secret_query["total"] == 0
        reason_query = await _search(http, q=reason, type="approval")
        assert reason_query["total"] == 0

        serialized = repr(page)
        assert secret not in serialized
        assert "secret_token" not in serialized
        assert reason not in serialized
        assert action.digest not in serialized
        assert "payload_ref" not in serialized
        assert "decision_comment" not in serialized

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt == 1

    asyncio.run(scenario())


def test_approval_search_hides_cross_project_items_counts_and_relationships() -> None:
    async def scenario() -> None:
        visible_project = new_id("project")
        hidden_project = new_id("project")
        approvals = ApprovalService()
        visible, _, visible_task = _seed_approval(
            approvals,
            project_id=visible_project,
            owner_id="visible-approval-owner",
            secret="visible-payload-secret",
            reason="Visible approval reason",
        )
        hidden, _, hidden_task = _seed_approval(
            approvals,
            project_id=hidden_project,
            owner_id="hidden-approval-owner",
            secret="hidden-payload-secret",
            reason="Hidden approval reason",
        )
        authorization = ApprovalSearchAuthorization(denied_project_id=hidden_project)
        _, http = await _stack(approvals, authorization)

        page = await _search(http, type="approval")
        assert page["total"] == 1
        assert {item["resource_id"] for item in _items(page)} == {visible.approval_id}
        serialized = repr(page)
        assert hidden.approval_id not in serialized
        assert hidden_task not in serialized
        assert hidden_project not in serialized
        assert "hidden-approval-owner" not in serialized
        assert "hidden-payload-secret" not in serialized
        assert "Hidden approval reason" not in serialized

        hidden_exact = await _search(http, id=hidden.approval_id, type="approval")
        assert hidden_exact["total"] == 0
        assert hidden.approval_id not in repr(hidden_exact)

        hidden_relationship = await _search(http, q=hidden_task, type="approval")
        assert hidden_relationship["total"] == 0
        hidden_project_search = await _search(http, project_id=hidden_project, type="approval")
        assert hidden_project_search["total"] == 0

        visible_exact = await _search(http, id=visible.approval_id, type="approval")
        assert visible_exact["total"] == 1
        visible_relationship = await _search(http, q=visible_task, type="approval")
        assert visible_relationship["total"] == 1

        assert any(call.action == "approval:list" for call in authorization.calls)

    asyncio.run(scenario())
