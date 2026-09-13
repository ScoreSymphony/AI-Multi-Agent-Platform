from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
    InstructionSource,
)
from ai_multi_agent_platform.context import OperationalContextSourceRequest
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.coordination import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import (
    ReferenceIncomingHandoffContextAdapter,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.handoffs import HandoffSourceKind
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.security import (
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
            role=InstructionSource(content="Use canonical predecessor Handoff context only."),
        ),
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_local_model(deployment) -> None:
    provider = FakeModelProvider()
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id="model-issue-889-handoff-context",
            display_name="Issue 889 Handoff Context model",
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


def test_dependency_handoff_is_materialized_after_restart_before_context_collection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        _install_local_model(deployment)

        task = await deployment.kernel.create_task(
            idempotency_key="issue-889-handoff-context:task",
            title="Restart-safe multi-agent Handoff",
            objective="Transfer the exact predecessor output after process restart.",
            owner_type="user",
            owner_id=admin.user_id,
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        producer_revision = deployment.agents.create_agent(
            _profile("Issue 889 producer", "researcher"),
            owner_ref=owner,
        )
        consumer_revision = deployment.agents.create_agent(
            _profile("Issue 889 consumer", "developer"),
            owner_ref=owner,
        )
        producer = AgentRevisionRef(producer_revision.agent_id, producer_revision.revision)
        consumer = AgentRevisionRef(consumer_revision.agent_id, consumer_revision.revision)
        for agent in (producer, consumer):
            deployment.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=_principal(agent),
                    actor_types=frozenset({ActorType.AGENT}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    resource_types=frozenset(
                        {
                            ResourceType.ARTIFACT,
                            ResourceType.GENERIC,
                        }
                    ),
                )
            )

        artifact_id = new_id("artifact")
        await deployment.kernel.attach_artifact(
            idempotency_key="issue-889-handoff-context:artifact",
            task_id=task.task_id,
            artifact_id=artifact_id,
        )
        operation = OperationContext(
            correlation_id=task.task_id,
            owner_type="user",
            owner_id=admin.user_id,
        )
        file_context = DataAccessContext(
            operation=operation,
            actor_ref=admin.user_id,
            task_id=task.task_id,
        )
        file_record = await deployment.files.create_file(
            b"issue 889 canonical predecessor artifact",
            file_context,
        )
        await deployment.files.link_artifact(file_record.file_id, artifact_id, file_context)

        plan_id = new_id("plan")
        producer_step_id = new_id("step")
        consumer_step_id = new_id("step")
        producer_run_id = new_id("run")
        consumer_run_id = new_id("run")
        deployment.coordination_repository.create_plan(
            Plan(
                id=plan_id,
                task_id=task.task_id,
                owner_ref=owner,
                revision=1,
                active=True,
            ),
            (
                Step(
                    id=producer_step_id,
                    plan_id=plan_id,
                    title="Produce canonical evidence",
                    owner_ref=owner,
                ),
                Step(
                    id=consumer_step_id,
                    plan_id=plan_id,
                    title="Consume canonical evidence",
                    owner_ref=owner,
                    depends_on=(producer_step_id,),
                ),
            ),
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

        producer_agent_run = await deployment.agent_runtime.start_agent(
            task_id=task.task_id,
            run_id=producer_run_id,
            agent_id=producer.agent_id,
            revision=producer.revision,
        )
        deployment.agent_runtime.finish_agent_run(
            producer_agent_run.agent_run_id,
            status=AgentRunStatus.SUCCEEDED,
            artifact_ids=(artifact_id,),
        )
        assert deployment.handoffs.service.list_handoffs_for_step(consumer_step_id) == ()

        restarted = build_single_node_deployment(config)
        _install_local_model(restarted)
        assert restarted.authorization.has_policy(_principal(producer))
        assert restarted.authorization.has_policy(_principal(consumer))
        await restarted.agent_runtime.start_agent(
            task_id=task.task_id,
            run_id=consumer_run_id,
            agent_id=consumer.agent_id,
            revision=consumer.revision,
        )

        adapter = ReferenceIncomingHandoffContextAdapter(restarted.handoffs)
        candidates = await adapter.collect(
            OperationalContextSourceRequest(
                task_id=task.task_id,
                run_id=consumer_run_id,
                agent_id=consumer.agent_id,
                agent_revision=consumer.revision,
                project_id=None,
                workspace_id=None,
                operation=operation,
                actor_ref=_principal(consumer),
                plan_id=plan_id,
                step_id=consumer_step_id,
            )
        )

        handoffs = restarted.handoffs.service.list_handoffs_for_step(consumer_step_id)
        assert len(handoffs) == 1
        handoff = handoffs[0]
        assert handoff.content.producer_step_id == producer_step_id
        assert handoff.content.consumer_step_id == consumer_step_id
        assert handoff.content.producer_run_id == producer_run_id
        assert handoff.content.producer == producer
        assert handoff.content.intended_consumer == consumer
        assert len(handoff.content.source_refs) == 1
        source_ref = handoff.content.source_refs[0]
        assert source_ref.kind is HandoffSourceKind.ARTIFACT
        assert source_ref.resource_id == artifact_id
        assert source_ref.revision == file_record.file_id
        assert source_ref.digest == file_record.sha256

        consumptions = restarted.handoffs.repository.list_consumptions_for_run(consumer_run_id)
        assert len(consumptions) == 1
        assert consumptions[0].handoff_id == handoff.handoff_id
        assert consumptions[0].consumer == consumer
        assert len(candidates) == 1
        assert candidates[0].source.source_id == handoff.handoff_id
        assert candidates[0].source.revision == str(handoff.revision)
        assert candidates[0].source.digest == handoff.content_digest

    asyncio.run(scenario())
