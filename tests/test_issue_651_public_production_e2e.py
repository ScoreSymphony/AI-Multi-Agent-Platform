from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    InstructionSource,
)
from ai_multi_agent_platform.context import ContextBudget, ContextSourceType
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.coordination.models import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.handoffs import HandoffContent, HandoffSourceKind, HandoffSourceRef
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content="Use only canonical production handoff context for this test."
            ),
        ),
    )


def _actor(agent: AgentRevisionRef) -> ActorIdentity:
    return ActorIdentity(
        f"agent:{agent.agent_id}@{agent.revision}",
        ActorType.AGENT,
    )


def _install_local_model(deployment) -> None:
    provider = FakeModelProvider()
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id="model-issue-651-public-e2e",
            display_name="Issue 651 public E2E local model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )


def test_public_single_node_handoff_survives_restart_and_executes_with_real_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        _install_local_model(deployment)

        project = deployment.scopes.create_project(
            key="issue-651-public-e2e",
            name="Issue 651 public production E2E",
            owner_type="user",
            owner_id=admin.user_id,
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue-651-public-e2e:task",
            title="Public production Agent Handoff",
            objective="Prove restart-safe Handoff execution through the public deployment.",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )

        owner = OwnerRef(type="user", id=admin.user_id)
        producer_revision = deployment.agents.create_agent(
            _profile("Issue 651 producer", "producer"),
            owner_ref=owner,
        )
        consumer_revision = deployment.agents.create_agent(
            _profile("Issue 651 consumer", "reviewer"),
            owner_ref=owner,
        )
        producer = AgentRevisionRef(producer_revision.agent_id, producer_revision.revision)
        consumer = AgentRevisionRef(consumer_revision.agent_id, consumer_revision.revision)
        producer_actor = _actor(producer)
        consumer_actor = _actor(consumer)

        for actor in (producer_actor, consumer_actor):
            deployment.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=actor.actor_id,
                    actor_types=frozenset({ActorType.AGENT}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    resource_types=frozenset(
                        {
                            ResourceType.ARTIFACT,
                            ResourceType.GENERIC,
                        }
                    ),
                    project_ids=frozenset({project.id}),
                )
            )

        artifact_id = new_id("artifact")
        await deployment.kernel.attach_artifact(
            idempotency_key="issue-651-public-e2e:artifact",
            task_id=task.task_id,
            artifact_id=artifact_id,
        )
        operation = OperationContext(
            correlation_id=f"issue-651-public-e2e:{task.task_id}",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        file_context = DataAccessContext(
            operation=operation,
            actor_ref=admin.user_id,
            task_id=task.task_id,
        )
        file_record = await deployment.files.create_file(
            b"canonical issue 651 artifact payload",
            file_context,
        )
        await deployment.files.link_artifact(file_record.file_id, artifact_id, file_context)
        artifact_subject = await deployment.verification_runtime.evidence.resolve_subject(
            task_id=task.task_id,
            subject_type="artifact",
            subject_id=artifact_id,
        )
        artifact_ref = HandoffSourceRef(
            HandoffSourceKind.ARTIFACT,
            artifact_id,
            revision=artifact_subject.revision,
            digest=artifact_subject.digest.removeprefix("sha256:"),
        )

        plan_id = new_id("plan")
        producer_step_id = new_id("step")
        consumer_step_id = new_id("step")
        producer_run_id = new_id("run")
        consumer_run_id = new_id("run")
        plan = Plan(
            id=plan_id,
            task_id=task.task_id,
            owner_ref=owner,
            revision=1,
            active=True,
        )
        producer_step = Step(
            id=producer_step_id,
            plan_id=plan_id,
            title="Produce canonical artifact",
            owner_ref=owner,
        )
        consumer_step = Step(
            id=consumer_step_id,
            plan_id=plan_id,
            title="Consume durable Handoff",
            owner_ref=owner,
            depends_on=(producer_step_id,),
        )
        deployment.coordination_repository.create_plan(
            plan,
            (producer_step, consumer_step),
            (
                StepCoordinationRecord(
                    task_id=task.task_id,
                    plan_id=plan_id,
                    plan_revision=1,
                    step_id=producer_step_id,
                    phase=CoordinationPhase.TERMINAL,
                    latest_run_id=producer_run_id,
                    current_attempt=1,
                ),
                StepCoordinationRecord(
                    task_id=task.task_id,
                    plan_id=plan_id,
                    plan_revision=1,
                    step_id=consumer_step_id,
                    phase=CoordinationPhase.ATTEMPT_ACTIVE,
                    dependency_ids=(producer_step_id,),
                    satisfied_dependency_ids=(producer_step_id,),
                    latest_run_id=consumer_run_id,
                    current_attempt=1,
                ),
            ),
        )

        producer_run = await deployment.agent_runtime.start_agent(
            task_id=task.task_id,
            run_id=producer_run_id,
            agent_id=producer.agent_id,
            revision=producer.revision,
        )
        assert producer_run.agent == producer

        handoff = await deployment.handoffs.runtime.create_handoff(
            HandoffContent(
                task_id=task.task_id,
                plan_id=plan_id,
                producer_step_id=producer_step_id,
                consumer_step_id=consumer_step_id,
                producer_run_id=producer_run_id,
                producer=producer,
                intended_consumer=consumer,
                objective="Transfer the exact producer result boundary after restart.",
                completed_work_summary="Producer completed the canonical artifact.",
                source_refs=(artifact_ref,),
                recommended_next_action="Continue from the durable Handoff.",
                requested_output="Consumer AgentRun bound to the recovered ContextBundle.",
            ),
            idempotency_key="issue-651-public-e2e:handoff",
            producer_actor=producer_actor,
            intended_consumer_actor=consumer_actor,
            operation=operation,
        )

        consumed_before_restart = await deployment.handoffs.runtime.consume_handoff(
            handoff.handoff_id,
            handoff.revision,
            consuming_run_id=consumer_run_id,
            consumer=consumer,
            consumer_actor=consumer_actor,
            operation=operation,
        )
        persisted_before_restart = deployment.handoffs.repository.list_consumptions_for_run(
            consumer_run_id
        )
        assert persisted_before_restart == (consumed_before_restart.consumption,)

        restarted = build_single_node_deployment(config)
        _install_local_model(restarted)
        recovered = restarted.handoffs.repository.list_consumptions_for_run(consumer_run_id)
        assert recovered == (consumed_before_restart.consumption,)
        recovered_handoff = restarted.handoffs.repository.get_handoff(
            handoff.handoff_id,
            handoff.revision,
        )
        assert recovered_handoff.content_digest == handoff.content_digest
        assert recovered_handoff.content.source_refs == (artifact_ref,)

        execution = await restarted.handoffs.runtime.start_consumer(
            handoff.handoff_id,
            handoff.revision,
            consuming_run_id=consumer_run_id,
            consumer=consumer,
            consumer_actor=consumer_actor,
            operation=operation,
            budget=ContextBudget(max_tokens=4096, max_bytes=16_384, max_items=16),
        )

        assert execution.runtime_context.consumption == consumed_before_restart.consumption
        assert execution.agent_run.agent == consumer
        assert execution.agent_run.run_id == consumer_run_id
        assert execution.context_binding.agent_run_id == execution.agent_run.agent_run_id
        assert execution.context_binding.context_bundle_id == execution.context_bundle.context_bundle_id
        assert execution.context_binding.context_bundle_digest == execution.context_bundle.digest
        assert execution.agent_run.verification_context["handoff_id"] == handoff.handoff_id
        assert execution.agent_run.verification_context["handoff_revision"] == handoff.revision
        assert execution.agent_run.verification_context["handoff_digest"] == handoff.content_digest

        handoff_entries = [
            entry
            for entry in execution.context_bundle.entries
            if entry.source.source_type is ContextSourceType.AGENT_HANDOFF
        ]
        assert len(handoff_entries) == 1
        entry = handoff_entries[0]
        assert entry.source.source_id == handoff.handoff_id
        assert entry.source.revision == str(handoff.revision)
        assert entry.source.digest == handoff.content_digest
        assert entry.inline_content is not None
        assert "canonical issue 651 artifact payload" not in entry.inline_content
        assert artifact_id in entry.inline_content

        reopened = build_single_node_deployment(config)
        stored_binding = reopened.handoffs.context_binding_repository.get(
            execution.agent_run.agent_run_id
        )
        stored_bundle = reopened.handoffs.context_bundle_repository.get(
            execution.context_bundle.context_bundle_id
        )
        assert stored_binding == execution.context_binding
        assert stored_bundle.digest == execution.context_bundle.digest
        assert reopened.handoffs.repository.list_consumptions_for_run(consumer_run_id) == (
            execution.runtime_context.consumption,
        )

    asyncio.run(scenario())
