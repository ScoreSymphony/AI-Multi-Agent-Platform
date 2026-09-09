from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS, bootstrap_standard_agents
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextBudget,
    ContextEntryRole,
    ContextLifecycleSourceRequest,
    ContextSourceType,
    ContextTrust,
    OperationalContextSourceRequest,
    TaskContextSourceAdapter,
    VerificationContextSourceAdapter,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.verification import (
    VerificationFinding,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


def _source_request(*, task_id: str, project_id: str) -> OperationalContextSourceRequest:
    return OperationalContextSourceRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        project_id=project_id,
        workspace_id=None,
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-680",
            project_id=project_id,
        ),
        actor_ref="user:issue-680",
    )


def test_completed_verification_findings_project_as_exact_untrusted_evidence() -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    source_run_id = new_id("run")
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="result-revision-3",
        digest="a" * 64,
    )
    policy = VerificationPolicy(
        name="Issue 680 verification context",
        stages=(
            VerificationStage(
                stage_id="quality",
                verifier_kind=VerifierKind.DETERMINISTIC,
            ),
        ),
    )
    verification = VerificationService()
    verification.register_policy(policy)
    verification_request = verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="quality",
        subject=subject,
        correlation_id=task_id,
        run_id=source_run_id,
        result_id=result_id,
        project_id=project_id,
    )
    verification_result = VerificationResult(
        verification_id=verification_request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref="deterministic:issue-680",
            kind=VerifierKind.DETERMINISTIC,
            read_only=True,
        ),
        outcome=VerificationOutcome.NEEDS_CHANGES,
        subject=subject,
        findings=(
            VerificationFinding(
                code="missing-evidence",
                message="Add exact repository evidence before retrying.",
                severity="warning",
                location_ref="result:summary",
            ),
        ),
        checks_executed=("issue_680_context_projection",),
        metadata={"private_adapter_note": "must-not-be-copied"},
    )
    verification.submit_result(verification_result)

    candidate = asyncio.run(
        VerificationContextSourceAdapter(verification).collect(
            _source_request(task_id=task_id, project_id=project_id)
        )
    )[0]

    assert candidate.source.source_type is ContextSourceType.VERIFICATION
    assert candidate.source.source_id == verification_request.verification_id
    assert candidate.source.revision == verification_result.verification_result_id
    assert candidate.source.digest == candidate.content_digest
    assert candidate.source.locator == (
        f"verification-result:{verification_result.verification_result_id}"
    )
    assert candidate.role is ContextEntryRole.EVIDENCE
    assert candidate.trust is ContextTrust.UNTRUSTED
    assert candidate.project_id == project_id

    payload = json.loads(candidate.inline_content or "{}")
    assert payload["verification_id"] == verification_request.verification_id
    assert payload["verification_result_id"] == verification_result.verification_result_id
    assert payload["run_id"] == source_run_id
    assert payload["subject"] == {
        "type": "result",
        "id": result_id,
        "revision": "result-revision-3",
        "digest": "a" * 64,
    }
    assert payload["outcome"] == VerificationOutcome.NEEDS_CHANGES.value
    assert payload["findings"] == [
        {
            "code": "missing-evidence",
            "message": "Add exact repository evidence before retrying.",
            "severity": "warning",
            "location_ref": "result:summary",
        }
    ]
    assert "private_adapter_note" not in (candidate.inline_content or "")
    assert "must-not-be-copied" not in (candidate.inline_content or "")


def test_pending_verification_request_does_not_become_context_evidence() -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest="b" * 64,
    )
    policy = VerificationPolicy(
        name="Pending verification",
        stages=(VerificationStage(stage_id="quality", verifier_kind=VerifierKind.HUMAN),),
    )
    verification = VerificationService()
    verification.register_policy(policy)
    verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="quality",
        subject=subject,
        correlation_id=task_id,
        result_id=result_id,
        project_id=project_id,
    )

    candidates = asyncio.run(
        VerificationContextSourceAdapter(verification).collect(
            _source_request(task_id=task_id, project_id=project_id)
        )
    )
    assert candidates == ()


def test_explicit_user_objective_remains_task_owned_canonical_context() -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    request = _source_request(task_id=task_id, project_id=project_id)
    objective = "Use the user-provided constraints exactly and cite the requested files."
    task = SimpleNamespace(
        id=task_id,
        title="User-requested task",
        description=objective,
        status=SimpleNamespace(value="running"),
        project_id=project_id,
        metadata={},
    )
    state = SimpleNamespace(task_id=task_id, task=task, revision=9)

    class _TaskRepository:
        async def get_task(self, requested_task_id: str):
            assert requested_task_id == task_id
            return state

    candidate = asyncio.run(TaskContextSourceAdapter(_TaskRepository()).collect(request))[0]

    assert candidate.source.source_type is ContextSourceType.TASK
    assert candidate.source.revision == "9"
    assert candidate.mandatory is True
    assert objective in (candidate.inline_content or "")
    assert candidate.source.source_type is not ContextSourceType.HUMAN


def test_public_single_node_real_adapters_preserve_historical_bundle_across_source_revision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        data_dir = tmp_path / "issue-680-conformance"
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        project = deployment.scopes.create_project(
            key="issue-680-project",
            name="Issue 680 context project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-680-workspace",
            project_id=project.id,
        )
        bootstrap_standard_agents(deployment.agents)
        assistant = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
            workspace_id=workspace.id,
            name="Issue 680 Context Assistant",
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue-680-create-task",
            title="Context revision proof",
            objective="First canonical user objective.",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
            actor_ref=admin.user_id,
        )
        run_id = new_id("run")
        operation = OperationContext(
            correlation_id=task.task_id,
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        source = ContextLifecycleSourceRequest(
            execution=SimpleNamespace(),
            task_id=task.task_id,
            run_id=run_id,
            agent_id=assistant.agent_id,
            agent_revision=assistant.revision,
            project_id=project.id,
            workspace_id=workspace.id,
            plan_id=None,
            step_id=None,
            objective=task.task.description,
            execution_binding=None,
        )
        binding_factory = deployment.context.lifecycle._binding_factory  # noqa: SLF001
        bindings = tuple(binding_factory(source))
        assert any(
            isinstance(binding.adapter, VerificationContextSourceAdapter)
            and binding.source_type is ContextSourceType.VERIFICATION
            for binding in bindings
        )

        def assembly_request() -> ContextAssemblyRequest:
            return ContextAssemblyRequest(
                task_id=task.task_id,
                run_id=run_id,
                agent_id=assistant.agent_id,
                agent_revision=assistant.revision,
                actor=ActorIdentity(actor_id=admin.user_id, actor_type=ActorType.HUMAN),
                operation=operation,
                candidates=(),
                budget=ContextBudget(
                    max_tokens=64_000,
                    max_bytes=256 * 1024,
                    max_items=128,
                ),
                workspace_id=workspace.id,
            )

        first = await deployment.context.assembly.assemble(
            assembly_request(),
            bindings=bindings,
        )
        first_task_entry = next(
            entry for entry in first.entries if entry.source.source_type is ContextSourceType.TASK
        )
        assert first_task_entry.source.revision == "1"

        updated = await deployment.kernel.update_task(
            idempotency_key="issue-680-update-task",
            task_id=task.task_id,
            objective="Second canonical user objective after an explicit Task revision.",
            actor_ref=admin.user_id,
        )
        assert updated.revision > task.revision

        second = await deployment.context.assembly.assemble(
            assembly_request(),
            bindings=bindings,
        )
        second_task_entry = next(
            entry for entry in second.entries if entry.source.source_type is ContextSourceType.TASK
        )
        assert second_task_entry.source.revision == str(updated.revision)
        assert second.digest != first.digest
        assert second.context_bundle_id != first.context_bundle_id

        historical = deployment.context.bundles.get(first.context_bundle_id)
        assert historical.digest == first.digest
        historical_task_entry = next(
            entry
            for entry in historical.entries
            if entry.source.source_type is ContextSourceType.TASK
        )
        assert historical_task_entry.source.revision == "1"

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False)
        )
        assert restarted.context.bundles.get(first.context_bundle_id).digest == first.digest
        assert restarted.context.bundles.get(second.context_bundle_id).digest == second.digest

    asyncio.run(scenario())
