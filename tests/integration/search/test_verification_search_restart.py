from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)
from ai_multi_agent_platform.verification import (
    CompletionState,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
    VerificationOutcome,
    VerificationPolicy,
    VerificationScope,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.control_plane import register_verification_control_plane


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


async def _stack(
    kernel_path: Path,
    verification_path: Path,
) -> tuple[
    PlatformKernel,
    SqliteVerificationService,
    SqliteVerificationCompletionAuthority,
    ControlPlane,
    ControlPlaneHTTP,
]:
    repository = SqliteKernelRepository(kernel_path)
    verification = SqliteVerificationService(verification_path)
    completion = SqliteVerificationCompletionAuthority(verification, verification_path)
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
        completion_authority=completion,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(),
    )
    register_verification_control_plane(control_plane, verification, completion)
    return kernel, verification, completion, control_plane, ControlPlaneHTTP(control_plane)


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


def _resource_ids(page: dict[str, object]) -> set[object]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return {item["resource_id"] for item in items}


def test_verification_search_survives_canonical_restart_and_rebuild(tmp_path: Path) -> None:
    async def scenario() -> None:
        kernel_path = tmp_path / "kernel.sqlite3"
        verification_path = tmp_path / "verification.sqlite3"
        project_id = new_id("project")
        capability_id = new_id("cap")
        agent_id = new_id("agent")

        kernel, verification, completion, _control_plane, _http = await _stack(
            kernel_path,
            verification_path,
        )
        task = await kernel.create_task(
            idempotency_key="issue-291-restart:create-task",
            title="Restart-safe Verification Search",
            objective="Prove Verification Search can rebuild from durable canonical state",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        )

        scoped_policy = verification.register_policy(
            VerificationPolicy(
                name="issue-291-scoped-policy",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
                scope=VerificationScope(
                    task_ids=(task.task_id,),
                    project_ids=(project_id,),
                    agent_ids=(agent_id,),
                    capability_ids=(capability_id,),
                ),
            )
        )
        request_policy = verification.register_policy(
            VerificationPolicy(
                name="issue-291-request-policy",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        result_id = new_id("result")
        subject = VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="restart-1",
            digest="sha256:issue-291-restart",
        )
        request = completion.request_verification(
            task_id=task.task_id,
            policy_id=request_policy.policy_id,
            policy_version=request_policy.version,
            stage_id="review",
            subject=subject,
            result_id=result_id,
            project_id=project_id,
            capability_ids=(capability_id,),
            correlation_id=task.task_id,
        )
        result = verification.record_human_review(
            request.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED

        # Reconstruct every canonical service from the durable stores. The SearchProvider
        # itself starts empty and must be rebuilt from the restored Kernel/Verification state.
        (
            restored_kernel,
            restored_verification,
            restored_completion,
            restored_control_plane,
            restored_http,
        ) = await _stack(kernel_path, verification_path)

        assert (await restored_kernel.get_task(task.task_id)).task_id == task.task_id
        assert restored_verification.get_request(request.verification_id).verification_id == (
            request.verification_id
        )
        assert (
            restored_completion.assess_task_completion(task.task_id).state
            is CompletionState.ACCEPTED
        )

        rebuilt = await restored_control_plane.rebuild_search_index(
            correlation_id="issue-291-restart-rebuild"
        )
        assert rebuilt > 0

        expected_by_type = {
            "verification": request.verification_id,
            "verification_result": result.verification_result_id,
            "verification_requirement": task.task_id,
        }
        for resource_type, resource_id in expected_by_type.items():
            exact = await _search(restored_http, type=resource_type, id=resource_id)
            assert _resource_ids(exact) == {resource_id}

            by_project = await _search(
                restored_http,
                type=resource_type,
                project_id=project_id,
            )
            assert resource_id in _resource_ids(by_project)

        scoped_policy_ref = f"{scoped_policy.policy_id}@{scoped_policy.version}"
        exact_policy = await _search(
            restored_http,
            type="verification_policy",
            id=scoped_policy_ref,
        )
        assert _resource_ids(exact_policy) == {scoped_policy_ref}

        for scope_value in (task.task_id, project_id, agent_id, capability_id):
            by_scope = await _search(
                restored_http,
                type="verification_policy",
                q=scope_value,
            )
            assert scoped_policy_ref in _resource_ids(by_scope)

        accepted_requirement = await _search(
            restored_http,
            type="verification_requirement",
            q="accepted",
        )
        assert task.task_id in _resource_ids(accepted_requirement)

    asyncio.run(scenario())
