from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import AuthorizationDecision
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
    VERIFICATION_COMMANDS,
    VERIFICATION_REQUIREMENT_COLLECTION,
    VERIFICATION_REVIEW_COLLECTION,
    register_verification_control_plane,
)


class ProjectScopedAuthorization(FakeAuthorizationProvider):
    def __init__(self) -> None:
        super().__init__()
        self.denied_project_id: str | None = None

    async def authorize(self, request):
        self.calls.append(request)
        allowed = (
            self.denied_project_id is None or request.context.project_id != self.denied_project_id
        )
        return AuthorizationDecision(allowed=allowed, reason="project-scope")


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-verification-86",
        "X-Correlation-Id": "correlation-verification-86",
        "X-Principal-Ref": "user:reviewer",
        "X-Owner-Type": "user",
        "X-Owner-Id": "owner-86",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def _stack():
    repository = InMemoryKernelRepository()
    verification = VerificationService()
    completion = VerificationCompletionAuthority(verification)
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
        completion_authority=completion,
    )
    authorization = ProjectScopedAuthorization()
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
    )
    register_verification_control_plane(control_plane, verification, completion)
    return (
        kernel,
        verification,
        completion,
        authorization,
        control_plane,
        ControlPlaneHTTP(control_plane),
    )


async def _human_request(kernel, verification, completion, *, project_id: str | None = None):
    task = await kernel.create_task(
        idempotency_key=f"create:{new_id('task')}",
        title="Review me",
        objective="Produce work requiring human verification",
        owner_type="user",
        owner_id="owner-86",
        project_id=project_id,
    )
    policy = verification.register_policy(
        VerificationPolicy(
            name=f"human-review-{task.task_id}",
            stages=(VerificationStage("human", VerifierKind.HUMAN),),
            max_repair_attempts=1,
        )
    )
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest=f"sha256:{task.task_id}",
    )
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human",
        subject=subject,
        result_id=result_id,
        project_id=project_id,
        correlation_id=task.task_id,
    )
    return task, request


def _items(body: object) -> list[object]:
    assert isinstance(body, dict)
    items = body.get("items")
    assert isinstance(items, list)
    return items


def test_verification_control_plane_registers_http_openapi_and_pending_review_queue() -> None:
    async def scenario() -> None:
        kernel, verification, completion, _, control_plane, http = await _stack()
        project_id = new_id("project")
        task, request = await _human_request(
            kernel,
            verification,
            completion,
            project_id=project_id,
        )

        assert VERIFICATION_COLLECTION in control_plane.registered_collections
        assert VERIFICATION_REVIEW_COLLECTION in control_plane.registered_collections
        assert VERIFICATION_REQUIREMENT_COLLECTION in control_plane.registered_collections
        assert set(VERIFICATION_COMMANDS).issubset(control_plane.registered_commands)

        reviews = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{VERIFICATION_REVIEW_COLLECTION}",
                headers=_headers(),
            )
        )
        assert reviews.status == 200
        items = _items(reviews.body)
        assert len(items) == 1
        assert isinstance(items[0], dict)
        assert items[0]["id"] == request.verification_id
        assert items[0]["task_id"] == task.task_id
        assert items[0]["status"] == "pending"
        assert items[0]["requested_verifier_kind"] == "human"

        requirement = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{VERIFICATION_REQUIREMENT_COLLECTION}/{task.task_id}",
                headers=_headers(),
            )
        )
        assert requirement.status == 200
        assert isinstance(requirement.body, dict)
        assert requirement.body["completion"]["state"] == CompletionState.WAITING.value
        assert requirement.body["subject"]["digest"] == f"sha256:{task.task_id}"

        openapi = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/openapi.json", headers=_headers())
        )
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        assert VERIFICATION_COLLECTION in openapi.body["x-registered-extension-collections"]
        assert "verification.accept" in openapi.body["x-registered-extension-commands"]
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert f"/api/v1/{VERIFICATION_REVIEW_COLLECTION}" in paths
        assert "/api/v1/commands/{command}" in paths

    asyncio.run(scenario())


def test_human_accept_is_canonical_idempotent_and_exposes_evidence() -> None:
    async def scenario() -> None:
        kernel, verification, completion, authorization, _, http = await _stack()
        project_id = new_id("project")
        task, request = await _human_request(
            kernel,
            verification,
            completion,
            project_id=project_id,
        )
        evidence_id = new_id("artifact")
        body = {
            "resource_ref": request.verification_id,
            "comment": "Reviewed and accepted",
            "evidence_artifact_ids": [evidence_id],
        }
        first = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/verification.accept",
                headers=_headers("accept-1"),
                body=body,
            )
        )
        second = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/verification.accept",
                headers=_headers("accept-1"),
                body=body,
            )
        )
        assert first.status == second.status == 200
        assert isinstance(first.body, dict) and isinstance(second.body, dict)
        first_result = first.body["verification_result"]
        second_result = second.body["verification_result"]
        assert isinstance(first_result, dict) and isinstance(second_result, dict)
        assert first_result["id"] == second_result["id"]
        assert first_result["outcome"] == VerificationOutcome.PASS.value
        assert first_result["evidence_artifact_ids"] == [evidence_id]
        assert first_result["verifier"]["ref"] == "user:reviewer"
        assert first_result["findings"][0]["message"] == "Reviewed and accepted"
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED

        queue = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{VERIFICATION_REVIEW_COLLECTION}",
                headers=_headers(),
            )
        )
        assert queue.status == 200
        assert _items(queue.body) == []

        replay_with_changed_payload = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/verification.accept",
                headers=_headers("accept-1"),
                body={
                    "resource_ref": request.verification_id,
                    "comment": "different payload",
                    "evidence_artifact_ids": [evidence_id],
                },
            )
        )
        assert replay_with_changed_payload.status == 409

        scoped_calls = [
            call
            for call in authorization.calls
            if call.action == "verification.accept" and call.context.project_id == project_id
        ]
        assert len(scoped_calls) >= 2
        assert all(call.resource_ref == request.verification_id for call in scoped_calls)
        assert all(call.request_payload_digest is not None for call in scoped_calls)

    asyncio.run(scenario())


def test_verification_reads_and_human_review_fail_closed_on_task_scope_denial() -> None:
    async def scenario() -> None:
        kernel, verification, completion, authorization, _, http = await _stack()
        project_id = new_id("project")
        _, request = await _human_request(
            kernel,
            verification,
            completion,
            project_id=project_id,
        )
        authorization.denied_project_id = project_id

        queue = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{VERIFICATION_REVIEW_COLLECTION}",
                headers=_headers(),
            )
        )
        assert queue.status == 200
        assert _items(queue.body) == []

        read = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{VERIFICATION_COLLECTION}/{request.verification_id}",
                headers=_headers(),
            )
        )
        assert read.status == 403

        accept = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/verification.accept",
                headers=_headers("denied-accept"),
                body={"resource_ref": request.verification_id},
            )
        )
        assert accept.status == 403
        assert verification.result_for(request.verification_id) is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("verification.reject", VerificationOutcome.FAIL),
        ("verification.request-changes", VerificationOutcome.NEEDS_CHANGES),
    ],
)
def test_human_reject_and_request_changes_are_canonical_commands(
    command: str,
    expected: VerificationOutcome,
) -> None:
    async def scenario() -> None:
        kernel, verification, completion, _, _, http = await _stack()
        task, request = await _human_request(kernel, verification, completion)
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{command}",
                headers=_headers(f"review-{expected.value}"),
                body={
                    "resource_ref": request.verification_id,
                    "comment": f"canonical {expected.value}",
                },
            )
        )
        assert response.status == 200
        result = verification.result_for(request.verification_id)
        assert result is not None
        assert result.outcome is expected
        decision = completion.assess_task_completion(task.task_id)
        if expected is VerificationOutcome.NEEDS_CHANGES:
            assert decision.state is CompletionState.REPAIR_REQUIRED
        else:
            assert decision.state is CompletionState.REJECTED

    asyncio.run(scenario())
