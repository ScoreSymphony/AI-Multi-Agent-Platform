from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS, bootstrap_standard_agents
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextBlockerReason,
    ContextBudget,
    ContextDataClassification,
    ContextLifecycleSourceRequest,
    ContextResolutionError,
    ContextSourceType,
    OperationalContextSourceRequest,
)
from ai_multi_agent_platform.contracts import DataClassification, OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.context_verification import (
    CanonicalVerificationContextClassificationResolver,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.verification import (
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


class _ArtifactFileProvider:
    def __init__(
        self,
        artifact_id: str,
        classification: DataClassification,
        *,
        file_id: str,
        sha256: str,
    ) -> None:
        self.record = SimpleNamespace(
            file_id=file_id,
            sha256=sha256,
            artifact_ids=(artifact_id,),
            classification=classification.value,
        )

    async def list_files(self, context):
        del context
        return (self.record,)


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


def _artifact_verification(
    *,
    artifact_id: str,
    file_id: str,
    sha256: str,
    task_id: str,
    project_id: str,
) -> tuple[object, VerificationResult]:
    subject = VerificationSubject(
        subject_type="artifact",
        subject_id=artifact_id,
        revision=file_id,
        digest=f"sha256:{sha256}",
    )
    policy = VerificationPolicy(
        name="Issue 680 artifact classification",
        stages=(VerificationStage(stage_id="quality", verifier_kind=VerifierKind.DETERMINISTIC),),
    )
    verification = VerificationService()
    verification.register_policy(policy)
    request = verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="quality",
        subject=subject,
        correlation_id=task_id,
        project_id=project_id,
    )
    result = VerificationResult(
        verification_id=request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref="deterministic:issue-680-classification",
            kind=VerifierKind.DETERMINISTIC,
            read_only=True,
        ),
        outcome=VerificationOutcome.PASS,
        subject=subject,
    )
    return request, result


@pytest.mark.parametrize(
    ("source_classification", "expected"),
    (
        (DataClassification.PUBLIC, ContextDataClassification.PUBLIC),
        (DataClassification.INTERNAL, ContextDataClassification.INTERNAL),
        (DataClassification.CONFIDENTIAL, ContextDataClassification.CONFIDENTIAL),
        (DataClassification.RESTRICTED, ContextDataClassification.RESTRICTED),
        (DataClassification.SECRET, ContextDataClassification.SECRET_REFERENCE),
    ),
)
def test_verification_artifact_classification_inherits_canonical_file_owner(
    source_classification: DataClassification,
    expected: ContextDataClassification,
) -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    artifact_id = new_id("artifact")
    file_id = new_id("file")
    sha256 = "d" * 64
    request, result = _artifact_verification(
        artifact_id=artifact_id,
        file_id=file_id,
        sha256=sha256,
        task_id=task_id,
        project_id=project_id,
    )
    resolver = CanonicalVerificationContextClassificationResolver(
        _ArtifactFileProvider(
            artifact_id,
            source_classification,
            file_id=file_id,
            sha256=sha256,
        )  # type: ignore[arg-type]
    )

    classification = asyncio.run(
        resolver.classify(
            _source_request(task_id=task_id, project_id=project_id),
            request,  # type: ignore[arg-type]
            result,
        )
    )

    assert classification is expected


@pytest.mark.parametrize("mismatch", ("revision", "digest"))
def test_verification_artifact_classification_fails_closed_on_subject_provenance_mismatch(
    mismatch: str,
) -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    artifact_id = new_id("artifact")
    file_id = new_id("file")
    sha256 = "e" * 64
    request, result = _artifact_verification(
        artifact_id=artifact_id,
        file_id=file_id,
        sha256=sha256,
        task_id=task_id,
        project_id=project_id,
    )
    provider_file_id = new_id("file") if mismatch == "revision" else file_id
    provider_sha256 = "f" * 64 if mismatch == "digest" else sha256
    resolver = CanonicalVerificationContextClassificationResolver(
        _ArtifactFileProvider(
            artifact_id,
            DataClassification.PUBLIC,
            file_id=provider_file_id,
            sha256=provider_sha256,
        )  # type: ignore[arg-type]
    )

    classification = asyncio.run(
        resolver.classify(
            _source_request(task_id=task_id, project_id=project_id),
            request,  # type: ignore[arg-type]
            result,
        )
    )

    assert classification is ContextDataClassification.SECRET_REFERENCE


def test_public_single_node_live_task_source_fails_closed_for_unauthorized_actor(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "issue-680-live-auth", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        project = deployment.scopes.create_project(
            key="issue-680-live-auth",
            name="Issue 680 live authorization",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-680-live-auth-workspace",
            project_id=project.id,
        )
        bootstrap_standard_agents(deployment.agents)
        assistant = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
            workspace_id=workspace.id,
            name="Issue 680 unauthorized context assistant",
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue-680-live-auth-task",
            title="Live source authorization",
            objective="Prove the public live Task source remains authorization-bound.",
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
        bindings = tuple(deployment.context.lifecycle._binding_factory(source))  # noqa: SLF001
        task_binding = next(
            binding for binding in bindings if binding.source_type is ContextSourceType.TASK
        )
        request = ContextAssemblyRequest(
            task_id=task.task_id,
            run_id=run_id,
            agent_id=assistant.agent_id,
            agent_revision=assistant.revision,
            actor=ActorIdentity("user:issue-680-unauthorized", ActorType.HUMAN),
            operation=operation,
            candidates=(),
            budget=ContextBudget(max_tokens=4096, max_bytes=16_384, max_items=16),
            workspace_id=workspace.id,
        )

        with pytest.raises(ContextResolutionError) as exc_info:
            await deployment.context.assembly.assemble(request, bindings=(task_binding,))

        assert exc_info.value.blocker.reason is ContextBlockerReason.MANDATORY_UNAUTHORIZED
        assert exc_info.value.blocker.source.source_type is ContextSourceType.TASK
        assert exc_info.value.blocker.source.source_id == task.task_id

    asyncio.run(scenario())
