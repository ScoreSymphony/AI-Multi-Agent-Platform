from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ai_multi_agent_platform.agents import AgentRunStatus
from ai_multi_agent_platform.context import ContextDataClassification, OperationalContextSourceRequest
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.deployment.context_verification import (
    CanonicalVerificationContextClassificationResolver,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    ProducerIdentity,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationRequest,
    VerificationResult,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


class _NoFiles:
    async def list_files(self, context):
        del context
        return ()


class _Evidence:
    def __init__(self, context: VerificationEvidenceContext) -> None:
        self.context = context

    async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
        assert task_id == self.context.task_id
        assert subject_type == self.context.subject.subject_type
        assert subject_id == self.context.subject.subject_id
        return self.context.subject

    async def resolve_context(
        self, *, task_id: str, subject_type: str, subject_id: str
    ) -> VerificationEvidenceContext:
        assert task_id == self.context.task_id
        assert subject_type == self.context.subject.subject_type
        assert subject_id == self.context.subject.subject_id
        return self.context

    async def validate_evidence_artifacts(
        self, *, task_id: str, artifact_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        assert task_id == self.context.task_id
        return artifact_ids


class _Agents:
    def __init__(self, record: object) -> None:
        self.record = record

    def list_agent_runs(self, run_id: str | None = None):
        if run_id is None or run_id == self.record.run_id:  # type: ignore[attr-defined]
            return (self.record,)
        return ()


class _Bindings:
    def __init__(self, binding: object) -> None:
        self.binding = binding

    def get(self, agent_run_id: str):
        if agent_run_id != self.binding.agent_run_id:  # type: ignore[attr-defined]
            raise KeyError(agent_run_id)
        return self.binding


class _Bundles:
    def __init__(self, bundle: object) -> None:
        self.bundle = bundle

    def get(self, context_bundle_id: str):
        if context_bundle_id != self.bundle.context_bundle_id:  # type: ignore[attr-defined]
            raise KeyError(context_bundle_id)
        return self.bundle


def _case(*, binding_digest: str = "bundle-digest"):
    task_id = new_id("task")
    project_id = new_id("project")
    run_id = new_id("run")
    result_id = new_id("result")
    producer_agent_id = new_id("agent")
    reviewer_agent_id = new_id("agent")
    agent_run_id = new_id("agent_run")
    bundle_id = new_id("context_bundle")

    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision=f"{run_id}:attempt:1",
        digest="sha256:result-snapshot",
    )
    producer = ProducerIdentity(
        actor_ref=f"agent:{producer_agent_id}@1",
        agent_id=producer_agent_id,
        agent_revision=1,
        model_config_id="model-local",
        provider_id="provider-local",
    )
    request = VerificationRequest(
        task_id=task_id,
        policy_id=new_id("verification_policy"),
        policy_version=1,
        stage_id="review",
        subject=subject,
        requested_verifier_kind=VerifierKind.AGENT,
        correlation_id=task_id,
        run_id=run_id,
        result_id=result_id,
        project_id=project_id,
        producer=producer,
    )
    result = VerificationResult(
        verification_id=request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref=f"agent:{reviewer_agent_id}@1",
            kind=VerifierKind.AGENT,
            agent_id=reviewer_agent_id,
            agent_revision=1,
            model_config_id="review-model-local",
            provider_id="provider-local",
            read_only=True,
        ),
        outcome=VerificationOutcome.NEEDS_CHANGES,
        subject=subject,
    )
    evidence_context = VerificationEvidenceContext(
        task_id=task_id,
        subject=subject,
        run_id=run_id,
        project_id=project_id,
        capability_ids=(),
        producer=producer,
    )
    agent_run = SimpleNamespace(
        agent_run_id=agent_run_id,
        task_id=task_id,
        run_id=run_id,
        status=AgentRunStatus.SUCCEEDED,
        agent=SimpleNamespace(agent_id=producer_agent_id, revision=1),
        selected_model_config_id="model-local",
        selected_provider_id="provider-local",
        result_ids=(result_id,),
    )
    binding = SimpleNamespace(
        agent_run_id=agent_run_id,
        run_id=run_id,
        task_id=task_id,
        agent_id=producer_agent_id,
        agent_revision=1,
        context_bundle_id=bundle_id,
        context_bundle_digest=binding_digest,
    )
    bundle = SimpleNamespace(
        context_bundle_id=bundle_id,
        digest="bundle-digest",
        run_id=run_id,
        task_id=task_id,
        agent_id=producer_agent_id,
        agent_revision=1,
        entries=(
            SimpleNamespace(data_classification=ContextDataClassification.INTERNAL),
        ),
    )
    source = OperationalContextSourceRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=producer_agent_id,
        agent_revision=1,
        project_id=project_id,
        workspace_id=None,
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-711-result-classification",
            project_id=project_id,
        ),
        actor_ref="user:issue-711-result-classification",
    )
    resolver = CanonicalVerificationContextClassificationResolver(
        _NoFiles(),  # type: ignore[arg-type]
        evidence=_Evidence(evidence_context),
        agents=_Agents(agent_run),  # type: ignore[arg-type]
        bundles=_Bundles(bundle),  # type: ignore[arg-type]
        run_bindings=_Bindings(binding),  # type: ignore[arg-type]
    )
    return resolver, source, request, result


def test_result_classification_inherits_exact_producer_context_bundle() -> None:
    resolver, source, request, result = _case()

    classification = asyncio.run(resolver.classify(source, request, result))

    assert classification is ContextDataClassification.INTERNAL


def test_result_classification_fails_closed_on_context_binding_digest_mismatch() -> None:
    resolver, source, request, result = _case(binding_digest="different-bundle-digest")

    classification = asyncio.run(resolver.classify(source, request, result))

    assert classification is ContextDataClassification.SECRET_REFERENCE


def test_result_classification_without_exact_provenance_is_reference_only() -> None:
    _resolver, source, request, result = _case()
    resolver = CanonicalVerificationContextClassificationResolver(_NoFiles())  # type: ignore[arg-type]

    classification = asyncio.run(resolver.classify(source, request, result))

    assert classification is ContextDataClassification.SECRET_REFERENCE
