from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
    InstructionSource,
)
from ai_multi_agent_platform.context import ContextSourceType, ReferenceContextOrchestratorAdapter
from ai_multi_agent_platform.contracts import HealthStatus, ModelRequest, ModelResponse
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import (
    REFERENCE_MULTI_AGENT_CONSTRAINT,
)
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, StepStatus, TaskStatus
from ai_multi_agent_platform.handoffs import HandoffSourceKind
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.planning import ProposalStatus
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)

_PASSWORD = "correct horse battery staple for issue 889"
_MODEL_ID = "model-issue-889-reference-provenance"
_STAGE_ID = "reference-golden-path-verification"


class _PassingReviewProvider(FakeModelProvider):
    def __init__(self) -> None:
        super().__init__(response_text="canonical reference output")
        self.review_calls: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.request_id.endswith(":review-model"):
            self.calls.append(request)
            self.review_calls.append(request)
            return ModelResponse(
                request_id=request.request_id,
                text=json.dumps({"outcome": "pass", "findings": []}),
                model_ref=self.model_ref,
            )
        return await super().generate(request)


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the reference multi-agent task.",
                version="1",
            )
        ),
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_local_model(deployment: Any) -> _PassingReviewProvider:
    provider = _PassingReviewProvider()
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id=_MODEL_ID,
            display_name="Issue 889 reference provenance model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                structured_output=True,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )
    return provider


def _create_agents(deployment: Any, owner: OwnerRef) -> dict[str, AgentRevisionRef]:
    refs: dict[str, AgentRevisionRef] = {}
    for role, name in (
        ("researcher", "Issue 889 Provenance Research Agent"),
        ("developer", "Issue 889 Provenance Execution Agent"),
        ("reviewer", "Issue 889 Provenance Review Agent"),
    ):
        revision = deployment.agents.create_agent(_profile(name, role), owner_ref=owner)
        ref = AgentRevisionRef(revision.agent_id, revision.revision)
        refs[role] = ref
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_principal(ref),
                actor_types=frozenset({ActorType.AGENT}),
                allowed_actions=frozenset(
                    {AuthorizationAction.READ, AuthorizationAction.RESULT_READ}
                ),
                resource_types=frozenset({ResourceType.ARTIFACT, ResourceType.GENERIC}),
            )
        )
    return refs


def _require_verification(deployment: Any, task_id: str, reviewer: AgentRevisionRef):
    policy = deployment.verification.register_policy(
        VerificationPolicy(
            name="Issue 889 provenance verification",
            stages=(VerificationStage(stage_id=_STAGE_ID, verifier_kind=VerifierKind.AGENT),),
            independence=ReviewerIndependence(producer_agent_must_differ=True),
            metadata={
                "automatic_reviewer": {
                    "enabled": True,
                    "subject_types": ["result"],
                    "stages": {
                        _STAGE_ID: {
                            "agent_id": reviewer.agent_id,
                            "agent_revision": reviewer.revision,
                        }
                    },
                }
            },
        )
    )
    deployment.verification_runtime.require_task(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    return policy


def test_reference_multi_agent_golden_path_persists_complete_canonical_provenance(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        provider = _install_local_model(deployment)
        admin = deployment.bootstrap_admin("issue-889-provenance-admin", _PASSWORD)
        refs = _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))

        task = await deployment.kernel.create_task(
            idempotency_key="issue-889:provenance:create",
            title="Reference multi-agent provenance",
            objective=(
                "Research the task, prepare an approach, execute it, review the exact result, "
                "and accept only verified canonical output."
            ),
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue-889:provenance:ready",
            task_id=task.task_id,
        )
        policy = _require_verification(deployment, task.task_id, refs["reviewer"])

        proposal = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-889:provenance:propose",
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        assert proposal.status is ProposalStatus.VALIDATED
        activated = await deployment.planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key="issue-889:provenance:activate",
            actor=ActorIdentity(admin.user_id, ActorType.HUMAN),
        )
        assert activated.activation_plan_id is not None

        completed = await deployment.kernel.get_task(task.task_id)
        assert completed.status is TaskStatus.SUCCEEDED
        assert completed.plan_ref == activated.activation_plan_id
        state = deployment.coordination_repository.get_plan(activated.activation_plan_id)
        assert state.plan.id == activated.activation_plan_id
        assert state.plan.task_id == task.task_id
        assert state.plan.revision == proposal.proposal.plan_revision
        assert set(completed.step_ids) == {step.id for step in state.steps}
        assert all(step.plan_id == state.plan.id for step in state.steps)
        assert all(step.status is StepStatus.SUCCEEDED for step in state.steps)

        steps = {step.title: step for step in state.steps}
        research = steps["Gather authoritative evidence"]
        approach = steps["Prepare an independent execution approach"]
        execute = steps["Produce the requested result"]
        review = steps["Review the exact produced result"]
        records = {
            step.id: deployment.coordination_repository.get_step_record(step.id)
            for step in state.steps
        }
        assert records[research.id].dependency_ids == ()
        assert records[approach.id].dependency_ids == ()
        assert set(records[execute.id].dependency_ids) == {research.id, approach.id}
        assert records[review.id].dependency_ids == (execute.id,)

        expected_refs = {
            research.id: refs["researcher"],
            approach.id: refs["developer"],
            execute.id: refs["developer"],
            review.id: refs["reviewer"],
        }
        run_ids: dict[str, str] = {}
        producer_runs: dict[str, Any] = {}
        result_ids: dict[str, str] = {}
        bundles: dict[str, Any] = {}
        for step in state.steps:
            record = records[step.id]
            assert record.latest_run_id is not None
            run_ids[step.id] = record.latest_run_id
            run = await deployment.kernel.get_run(task.task_id, record.latest_run_id)
            assert run.task_id == task.task_id
            assert run.run.subject_type == "step"
            assert run.run.subject_id == step.id
            assert run.status is RunStatus.SUCCEEDED

            ordinary_runs = [
                item
                for item in deployment.agents.repository.list_agent_runs(record.latest_run_id)
                if "verification_id" not in item.verification_context
            ]
            assert len(ordinary_runs) == 1
            producer = ordinary_runs[0]
            producer_runs[step.id] = producer
            assert producer.status is AgentRunStatus.SUCCEEDED
            assert producer.task_id == task.task_id
            assert producer.run_id == run.run_id
            assert producer.agent == expected_refs[step.id]
            assert producer.selected_model_config_id == _MODEL_ID
            assert producer.selected_provider_id == provider.descriptor.provider_id
            assert (
                producer.orchestrator_adapter_id == ReferenceContextOrchestratorAdapter.adapter_id
            )
            assert run.output.get("agent_run_id") == producer.agent_run_id
            if run.backend_ref is not None:
                assert run.backend_ref == f"agent-run:{producer.agent_run_id}"
            assert len(producer.result_ids) == 1
            result_id = producer.result_ids[0]
            result_ids[step.id] = result_id
            assert run.output.get("result_id") == result_id

            binding = deployment.context.run_bindings.get(producer.agent_run_id)
            bundle = deployment.context.bundles.get(binding.context_bundle_id)
            bundles[step.id] = bundle
            assert binding.agent_run_id == producer.agent_run_id
            assert binding.run_id == run.run_id
            assert binding.task_id == task.task_id
            assert binding.agent_id == producer.agent.agent_id
            assert binding.agent_revision == producer.agent.revision
            assert binding.orchestrator_adapter_id == ReferenceContextOrchestratorAdapter.adapter_id
            assert binding.context_bundle_digest == bundle.digest
            assert bundle.task_id == task.task_id
            assert bundle.run_id == run.run_id
            assert bundle.agent_id == producer.agent.agent_id
            assert bundle.agent_revision == producer.agent.revision
            assert bundle.plan_id == state.plan.id
            assert bundle.step_id == step.id

        assert set(run_ids.values()).issubset(set(completed.run_ids))

        expected_handoff_producers = {
            execute.id: {research.id, approach.id},
            review.id: {execute.id},
        }
        for consumer_step_id, expected_producer_ids in expected_handoff_producers.items():
            handoffs = tuple(
                handoff
                for handoff in deployment.handoffs.service.list_handoffs_for_step(consumer_step_id)
                if handoff.content.consumer_step_id == consumer_step_id
            )
            assert {handoff.content.producer_step_id for handoff in handoffs} == (
                expected_producer_ids
            )
            consumption_by_id = {
                item.handoff_id: item
                for item in deployment.handoffs.repository.list_consumptions_for_run(
                    run_ids[consumer_step_id]
                )
            }
            bundle = bundles[consumer_step_id]
            bundle_handoff_entries = {
                entry.source.source_id: entry
                for entry in bundle.entries
                if entry.source.source_type is ContextSourceType.AGENT_HANDOFF
            }
            assert set(bundle_handoff_entries) == {handoff.handoff_id for handoff in handoffs}

            for handoff in handoffs:
                producer_step_id = handoff.content.producer_step_id
                assert handoff.content.task_id == task.task_id
                assert handoff.content.plan_id == state.plan.id
                assert handoff.content.producer_run_id == run_ids[producer_step_id]
                assert handoff.content.producer == producer_runs[producer_step_id].agent
                assert handoff.content.intended_consumer == producer_runs[consumer_step_id].agent
                assert len(handoff.content.source_refs) == 1
                source = handoff.content.source_refs[0]
                assert source.kind is HandoffSourceKind.RESULT
                assert source.resource_id == result_ids[producer_step_id]
                subject = await deployment.verification_runtime.evidence.resolve_subject(
                    task_id=task.task_id,
                    subject_type="result",
                    subject_id=source.resource_id,
                )
                assert source.revision == subject.revision
                assert source.digest == subject.digest.removeprefix("sha256:")

                consumption = consumption_by_id[handoff.handoff_id]
                assert consumption.handoff_revision == handoff.revision
                assert consumption.handoff_digest == handoff.content_digest
                assert consumption.consuming_run_id == run_ids[consumer_step_id]
                assert consumption.consumer == producer_runs[consumer_step_id].agent

                entry = bundle_handoff_entries[handoff.handoff_id]
                assert entry.source.revision == str(handoff.revision)
                assert entry.source.digest == handoff.content_digest
                assert entry.metadata["task_id"] == task.task_id
                assert entry.metadata["plan_id"] == state.plan.id
                assert entry.metadata["producer_step_id"] == producer_step_id
                assert entry.metadata["consumer_step_id"] == consumer_step_id
                assert entry.metadata["consuming_run_id"] == run_ids[consumer_step_id]

        execute_result_id = result_ids[execute.id]
        execute_subject = await deployment.verification_runtime.evidence.resolve_subject(
            task_id=task.task_id,
            subject_type="result",
            subject_id=execute_result_id,
        )
        execute_verifications = [
            (request, result)
            for request, result in deployment.verification.history(task_id=task.task_id)
            if request.policy_id == policy.policy_id
            and request.policy_version == policy.version
            and request.stage_id == _STAGE_ID
            and request.subject == execute_subject
        ]
        assert len(execute_verifications) == 1
        verification_request, verification_result = execute_verifications[0]
        assert verification_request.run_id == run_ids[execute.id]
        assert verification_request.result_id == execute_result_id
        assert verification_request.producer is not None
        assert verification_request.producer.agent_id == refs["developer"].agent_id
        assert verification_request.producer.agent_revision == refs["developer"].revision
        assert verification_result is not None
        assert verification_result.outcome is VerificationOutcome.PASS
        assert verification_result.subject == execute_subject

        succeeded_events = [
            event
            for event in await deployment.kernel.history(task.task_id)
            if event.event_type == "task.succeeded"
        ]
        assert len(succeeded_events) == 1
        assert succeeded_events[0].occurred_at >= verification_result.completed_at

        assert isinstance(provider, FakeModelProvider)
        assert deployment.models.get_model(_MODEL_ID).location is ModelLocation.LOCAL
        assert all(
            producer.orchestrator_adapter_id == ReferenceContextOrchestratorAdapter.adapter_id
            for producer in producer_runs.values()
        )
        assert all(
            "hermes" not in (producer.orchestrator_adapter_id or "").lower()
            and "forge" not in (producer.orchestrator_adapter_id or "").lower()
            for producer in producer_runs.values()
        )

    asyncio.run(scenario())
