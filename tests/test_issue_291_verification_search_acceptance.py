from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.search import SearchDocument, SearchQuery
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
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from ai_multi_agent_platform.verification.control_plane import register_verification_control_plane


class VerificationAcceptanceAuthorization(FakeAuthorizationProvider):
    def __init__(self, *, deny_policy_search: bool = False) -> None:
        super().__init__()
        self.deny_policy_search = deny_policy_search

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if self.deny_policy_search and request.action == "verification-policy:list":
            return AuthorizationDecision(allowed=False, reason="policy-search-denied")
        return AuthorizationDecision(allowed=True, reason="visible")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


async def _stack(
    authorization: VerificationAcceptanceAuthorization | None = None,
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
        authorization=authorization or VerificationAcceptanceAuthorization(),
    )
    register_verification_control_plane(control_plane, verification, completion)
    return kernel, verification, completion, control_plane, ControlPlaneHTTP(control_plane)


async def _verification_request(
    kernel: PlatformKernel,
    verification: VerificationService,
    completion: VerificationCompletionAuthority,
    *,
    label: str,
    verifier_kind: VerifierKind = VerifierKind.HUMAN,
    with_run: bool = False,
    revision: str = "1",
    digest: str | None = None,
):
    project_id = new_id("project")
    task = await kernel.create_task(
        idempotency_key=f"issue-291:{label}:task",
        title=f"Verification acceptance {label}",
        objective="Exercise issue 291 acceptance criteria",
        owner_type="user",
        owner_id="alice",
        project_id=project_id,
    )
    run_id = None
    if with_run:
        await kernel.ready_task(
            idempotency_key=f"issue-291:{label}:ready",
            task_id=task.task_id,
        )
        run = await kernel.create_run(
            idempotency_key=f"issue-291:{label}:run",
            task_id=task.task_id,
        )
        run_id = run.run_id

    policy = verification.register_policy(
        VerificationPolicy(
            name=f"issue-291-{label}",
            stages=(VerificationStage("review", verifier_kind),),
            max_repair_attempts=1,
        )
    )
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision=revision,
        digest=digest or f"sha256:issue-291-{label}",
    )
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        run_id=run_id,
        result_id=result_id,
        project_id=project_id,
        correlation_id=task.task_id,
    )
    return task, request, policy, subject, run_id


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


def test_pending_fail_and_needs_changes_are_discoverable_and_updates_propagate() -> None:
    async def scenario() -> None:
        kernel, verification, completion, _control_plane, http = await _stack()
        (
            _pending_task,
            pending_request,
            _pending_policy,
            _pending_subject,
            _,
        ) = await _verification_request(
            kernel,
            verification,
            completion,
            label="pending",
        )
        _fail_task, fail_request, _fail_policy, _fail_subject, _ = await _verification_request(
            kernel,
            verification,
            completion,
            label="fail",
        )
        (
            _changes_task,
            changes_request,
            _changes_policy,
            _changes_subject,
            _,
        ) = await _verification_request(
            kernel,
            verification,
            completion,
            label="changes",
        )

        pending = await _search(http, type="verification", status="pending")
        pending_ids = {item["resource_id"] for item in _items(pending)}
        assert pending_request.verification_id in pending_ids
        assert fail_request.verification_id in pending_ids
        assert changes_request.verification_id in pending_ids

        before = await _search(http, type="verification", id=fail_request.verification_id)
        assert _items(before)[0]["status"] == "pending"

        fail_result = verification.record_human_review(
            fail_request.verification_id,
            reviewer_ref="user:reviewer-fail",
            outcome=VerificationOutcome.FAIL,
        )
        changes_result = verification.record_human_review(
            changes_request.verification_id,
            reviewer_ref="user:reviewer-changes",
            outcome=VerificationOutcome.NEEDS_CHANGES,
        )

        after = await _search(http, type="verification", id=fail_request.verification_id)
        assert _items(after)[0]["status"] == "completed"
        assert _items(after)[0]["updated_at"] == fail_result.completed_at.isoformat()

        failed = await _search(http, type="verification_result", status="fail")
        assert {item["resource_id"] for item in _items(failed)} == {
            fail_result.verification_result_id
        }
        failed_text = await _search(http, type="verification_result", q="fail")
        assert fail_result.verification_result_id in {
            item["resource_id"] for item in _items(failed_text)
        }

        needs_changes = await _search(
            http,
            type="verification_result",
            status="needs_changes",
        )
        assert {item["resource_id"] for item in _items(needs_changes)} == {
            changes_result.verification_result_id
        }
        changes_text = await _search(http, type="verification_result", q="needs_changes")
        assert changes_result.verification_result_id in {
            item["resource_id"] for item in _items(changes_text)
        }

        still_pending = await _search(http, type="verification", status="pending")
        assert {item["resource_id"] for item in _items(still_pending)} == {
            pending_request.verification_id
        }

    asyncio.run(scenario())


def test_run_relationship_and_exact_subject_provenance_are_preserved_safely() -> None:
    async def scenario() -> None:
        digest = "sha256:issue-291-canonical-digest"
        revision = "revision-42"
        kernel, verification, completion, _control_plane, http = await _stack()
        _task, request, _policy, subject, run_id = await _verification_request(
            kernel,
            verification,
            completion,
            label="run-provenance",
            with_run=True,
            revision=revision,
            digest=digest,
        )
        assert run_id is not None
        result = verification.record_human_review(
            request.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )

        request_by_run = await _search(http, type="verification", q=run_id)
        assert {item["resource_id"] for item in _items(request_by_run)} == {request.verification_id}
        result_by_run = await _search(http, type="verification_result", q=run_id)
        assert {item["resource_id"] for item in _items(result_by_run)} == {
            result.verification_result_id
        }
        result_by_revision = await _search(
            http,
            type="verification_result",
            q=revision,
        )
        assert {item["resource_id"] for item in _items(result_by_revision)} == {
            result.verification_result_id
        }
        digest_search = await _search(http, q=digest)
        assert digest_search["total"] == 0

        canonical = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/verifications/{request.verification_id}",
                headers=_headers(),
            )
        )
        assert canonical.status == 200, canonical.body
        assert isinstance(canonical.body, dict)
        canonical_subject = canonical.body["subject"]
        assert isinstance(canonical_subject, dict)
        assert canonical_subject["revision"] == subject.revision
        assert canonical_subject["digest"] == subject.digest

    asyncio.run(scenario())


def test_search_provider_state_cannot_determine_verification_or_completion_authority() -> None:
    async def scenario() -> None:
        kernel, verification, completion, control_plane, _http = await _stack()
        task, request, _policy, _subject, _ = await _verification_request(
            kernel,
            verification,
            completion,
            label="authority",
        )
        before = completion.assess_task_completion(task.task_id)
        assert before.state is CompletionState.WAITING
        assert verification.result_for(request.verification_id) is None

        forged = SearchDocument(
            resource_type="verification_result",
            resource_id=new_id("verification_result"),
            title="Forged verification result",
            project_id=task.task.project_id,
            owner_type="user",
            owner_id="alice",
            status="pass",
            keywords=("pass", "accepted", request.verification_id),
        )
        operation = OperationContext(correlation_id="issue-291-forged-search")
        await control_plane.search_provider.rebuild((forged,), operation)
        forged_page = await control_plane.search_provider.search(
            SearchQuery(text="pass"),
            operation,
        )
        assert len(forged_page.items) == 1
        assert forged_page.items[0].status == "pass"

        after = completion.assess_task_completion(task.task_id)
        assert after.state is CompletionState.WAITING
        assert after.blocking_verification_ids == before.blocking_verification_ids
        assert verification.result_for(request.verification_id) is None

    asyncio.run(scenario())


def test_safe_machine_verifier_metadata_is_discoverable_but_policy_search_can_be_denied() -> None:
    async def machine_scenario() -> None:
        kernel, verification, completion, _control_plane, http = await _stack()
        _task, request, _policy, subject, _ = await _verification_request(
            kernel,
            verification,
            completion,
            label="machine-verifier",
            verifier_kind=VerifierKind.AGENT,
        )
        agent_id = new_id("agent")
        model_config_id = "model-config-issue-291"
        provider_id = "provider-issue-291"
        result = verification.submit_result(
            VerificationResult(
                verification_id=request.verification_id,
                verifier=VerifierIdentity(
                    verifier_ref=f"agent:{agent_id}:7",
                    kind=VerifierKind.AGENT,
                    agent_id=agent_id,
                    agent_revision=7,
                    model_config_id=model_config_id,
                    provider_id=provider_id,
                    read_only=True,
                ),
                outcome=VerificationOutcome.PASS,
                subject=subject,
            )
        )
        for query_value in (agent_id, model_config_id, provider_id, "agent"):
            page = await _search(http, type="verification_result", q=query_value)
            assert result.verification_result_id in {item["resource_id"] for item in _items(page)}

    async def policy_denial_scenario() -> None:
        authorization = VerificationAcceptanceAuthorization(deny_policy_search=True)
        kernel, verification, completion, _control_plane, http = await _stack(authorization)
        _task, request, policy, _subject, _ = await _verification_request(
            kernel,
            verification,
            completion,
            label="policy-denied",
        )
        policy_ref = f"{policy.policy_id}@{policy.version}"

        hidden_exact = await _search(http, type="verification_policy", id=policy_ref)
        hidden_text = await _search(http, type="verification_policy", q=policy.policy_id)
        assert hidden_exact["total"] == 0
        assert hidden_text["total"] == 0
        assert _items(hidden_exact) == []
        assert _items(hidden_text) == []

        request_page = await _search(http, type="verification", id=request.verification_id)
        assert request_page["total"] == 1

    asyncio.run(machine_scenario())
    asyncio.run(policy_denial_scenario())
