from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)
from ai_multi_agent_platform.verification import (
    CompletionState,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.control_plane import (
    VERIFICATION_COLLECTION,
    VERIFICATION_POLICY_COLLECTION,
    VERIFICATION_REQUIREMENT_COLLECTION,
    VERIFICATION_REVIEW_COLLECTION,
    register_verification_control_plane,
)

FINDING_SECRET = "verification-finding-secret-must-not-be-searchable"
DIGEST_SECRET = "sha256:verification-digest-secret-must-not-be-searchable"
REVIEWER_SECRET = "user:verification-reviewer-secret-must-not-be-searchable"


class VerificationSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str | None = None) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action in {
            "verification-policy:list",
            "verification:list",
            "verification-requirement:list",
        }:
            if request.principal_ref == "local:anonymous":
                return AuthorizationDecision(allowed=False, reason="synthetic-rebuild-actor")
            if (
                self.denied_project_id is not None
                and request.context.project_id == self.denied_project_id
            ):
                return AuthorizationDecision(allowed=False, reason="hidden-project")
        return AuthorizationDecision(allowed=True, reason="visible")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


async def _stack(
    authorization: VerificationSearchAuthorization,
) -> tuple[
    PlatformKernel,
    VerificationService,
    VerificationCompletionAuthority,
    ControlPlane,
    ControlPlaneHTTP,
]:
    repository = InMemoryKernelRepository()
    verification = VerificationService()
    completion = VerificationCompletionAuthority(verification)
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
        completion_authority=completion,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
    )
    register_verification_control_plane(control_plane, verification, completion)
    return kernel, verification, completion, control_plane, ControlPlaneHTTP(control_plane)


async def _request(
    kernel: PlatformKernel,
    verification: VerificationService,
    completion: VerificationCompletionAuthority,
    *,
    owner_id: str,
    project_id: str,
    digest: str,
    capability_id: str | None = None,
    artifact_id: str | None = None,
):
    task = await kernel.create_task(
        idempotency_key=f"verification-search:{owner_id}:{project_id}",
        title=f"Verification search task {owner_id}",
        objective="Exercise canonical Verification discovery",
        owner_type="user",
        owner_id=owner_id,
        project_id=project_id,
    )
    requested_capability_ref = f"review.{owner_id}"
    policy = verification.register_policy(
        VerificationPolicy(
            name=f"verification-search-{owner_id}",
            stages=(
                VerificationStage(
                    "human-review",
                    VerifierKind.HUMAN,
                    capability_ref=requested_capability_ref,
                ),
            ),
            max_repair_attempts=1,
        )
    )
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest=digest,
    )
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human-review",
        subject=subject,
        result_id=result_id,
        artifact_ids=() if artifact_id is None else (artifact_id,),
        project_id=project_id,
        capability_ids=() if capability_id is None else (capability_id,),
        correlation_id=task.task_id,
    )
    return task, request, requested_capability_ref


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(),
            query=query,
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_verification_requests_and_requirements_are_searchable_without_review_evidence_leaks() -> (
    None
):
    async def scenario() -> None:
        authorization = VerificationSearchAuthorization()
        kernel, verification, completion, control_plane, http = await _stack(authorization)
        project_id = new_id("project")
        capability_id = new_id("cap")
        artifact_id = new_id("artifact")
        task, request, requested_capability_ref = await _request(
            kernel,
            verification,
            completion,
            owner_id="alice",
            project_id=project_id,
            digest=DIGEST_SECRET,
            capability_id=capability_id,
            artifact_id=artifact_id,
        )
        policy = verification.get_policy(request.policy_id, request.policy_version)
        result = verification.record_human_review(
            request.verification_id,
            reviewer_ref=REVIEWER_SECRET,
            outcome=VerificationOutcome.PASS,
            comment=FINDING_SECRET,
            evidence_artifact_ids=(artifact_id,),
        )

        exact = await _search(http, type="verification", id=request.verification_id)
        assert exact["total"] == 1
        item = _items(exact)[0]
        assert item["resource_id"] == request.verification_id
        assert item["title"] == f"Verification for task {task.task_id}"
        assert item["project_id"] == project_id
        assert item["owner_type"] == "user"
        assert item["owner_id"] == "alice"
        assert item["status"] == "completed"
        assert item["updated_at"] == result.completed_at.isoformat()
        assert (
            item["canonical_ref"] == f"/api/v1/{VERIFICATION_COLLECTION}/{request.verification_id}"
        )
        assert item["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": VERIFICATION_COLLECTION,
        }

        for query_value in (
            task.task_id,
            request.result_id,
            result.verification_result_id,
            policy.policy_id,
            str(policy.version),
            artifact_id,
            capability_id,
            "human-review",
            "human",
            requested_capability_ref,
            "pass",
        ):
            page = await _search(http, type="verification", q=str(query_value))
            assert page["total"] == 1, (query_value, page)

        policy_ref = f"{policy.policy_id}@{policy.version}"
        policy_page = await _search(http, type="verification_policy", id=policy_ref)
        assert policy_page["total"] == 1
        policy_item = _items(policy_page)[0]
        assert policy_item["resource_id"] == policy_ref
        assert policy_item["title"] == policy.name
        assert policy_item["version"] == str(policy.version)
        assert policy_item["canonical_ref"] == (
            f"/api/v1/{VERIFICATION_POLICY_COLLECTION}/{policy_ref}"
        )
        for query_value in (
            policy.policy_id,
            policy.name,
            requested_capability_ref,
            "human",
            "pass",
        ):
            page = await _search(http, type="verification_policy", q=str(query_value))
            assert page["total"] == 1, (query_value, page)

        requirement = await _search(
            http,
            type="verification_requirement",
            id=task.task_id,
        )
        assert requirement["total"] == 1
        requirement_item = _items(requirement)[0]
        assert requirement_item["title"] == f"Verification requirement for task {task.task_id}"
        assert requirement_item["project_id"] == project_id
        assert requirement_item["owner_id"] == "alice"
        assert requirement_item["canonical_ref"] == (
            f"/api/v1/{VERIFICATION_REQUIREMENT_COLLECTION}/{task.task_id}"
        )
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED
        accepted = await _search(http, type="verification_requirement", q="accepted")
        assert accepted["total"] == 1

        # The pending-review collection is a derived queue over the same canonical
        # verification resource and must never become a second Search identity.
        review_type = await _search(http, type="verification-review")
        assert review_type["total"] == 0
        assert VERIFICATION_REVIEW_COLLECTION not in json.dumps(exact, sort_keys=True)

        for private_value in (FINDING_SECRET, DIGEST_SECRET, REVIEWER_SECRET):
            page = await _search(http, q=private_value)
            assert page["total"] == 0, (private_value, page)

        serialized = json.dumps(
            {"verification": exact, "policy": policy_page, "requirement": requirement},
            sort_keys=True,
        )
        assert FINDING_SECRET not in serialized
        assert DIGEST_SECRET not in serialized
        assert REVIEWER_SECRET not in serialized

        # Rebuild succeeds even though this authorization provider rejects the
        # synthetic local:anonymous actor. Search enumeration is actor-independent;
        # authorization happens on the caller-visible result boundary.
        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt > 0
        anonymous_verification_calls = [
            call
            for call in authorization.calls
            if call.principal_ref == "local:anonymous"
            and call.action
            in {
                "verification-policy:list",
                "verification:list",
                "verification-requirement:list",
            }
        ]
        assert anonymous_verification_calls == []

    asyncio.run(scenario())


def test_verification_search_indexes_all_task_scopes_but_hides_denied_project_existence() -> None:
    async def scenario() -> None:
        hidden_project = new_id("project")
        authorization = VerificationSearchAuthorization(denied_project_id=hidden_project)
        kernel, verification, completion, control_plane, http = await _stack(authorization)

        visible_project = new_id("project")
        visible_task, visible_request, _ = await _request(
            kernel,
            verification,
            completion,
            owner_id="alice",
            project_id=visible_project,
            digest="sha256:visible-verification",
        )
        hidden_task, hidden_request, _ = await _request(
            kernel,
            verification,
            completion,
            owner_id="bob",
            project_id=hidden_project,
            digest="sha256:hidden-verification",
        )

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt > 0

        visible = await _search(http, type="verification", id=visible_request.verification_id)
        assert visible["total"] == 1
        assert _items(visible)[0]["owner_id"] == "alice"

        hidden_verification = await _search(
            http,
            type="verification",
            id=hidden_request.verification_id,
        )
        hidden_requirement = await _search(
            http,
            type="verification_requirement",
            id=hidden_task.task_id,
        )
        hidden_project_page = await _search(
            http,
            type="verification",
            project_id=hidden_project,
        )
        assert hidden_verification["total"] == 0
        assert hidden_requirement["total"] == 0
        assert hidden_project_page["total"] == 0

        serialized = json.dumps(
            {
                "verification": hidden_verification,
                "requirement": hidden_requirement,
                "project": hidden_project_page,
            },
            sort_keys=True,
        )
        assert hidden_request.verification_id not in serialized
        assert hidden_task.task_id not in serialized
        assert hidden_project not in serialized
        assert "bob" not in serialized

        denied_calls = [
            call
            for call in authorization.calls
            if call.action in {"verification:list", "verification-requirement:list"}
            and call.context.project_id == hidden_project
        ]
        assert denied_calls
        assert all(call.context.owner_type == "user" for call in denied_calls)
        assert all(call.context.owner_id == "bob" for call in denied_calls)
        assert any(call.action == "verification:list" for call in denied_calls)
        assert any(call.action == "verification-requirement:list" for call in denied_calls)

        visible_requirement = await _search(
            http,
            type="verification_requirement",
            id=visible_task.task_id,
        )
        assert visible_requirement["total"] == 1

    asyncio.run(scenario())
