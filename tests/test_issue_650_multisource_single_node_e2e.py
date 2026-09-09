from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_step_execution_bindings,
)
from ai_multi_agent_platform.context import ContextSourceType
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.coordination import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, Plan, Provenance, Step, new_id
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.repositories import RepositoryRunProvenance
from ai_multi_agent_platform.research import ResearchClass, ResearchSourceType
from ai_multi_agent_platform.research.models import EvidenceRelation
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.skills import SkillBundle
from ai_multi_agent_platform.testing import FakeModelProvider


def test_public_single_node_multisource_context_flows_through_restart_and_inspection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "issue-650-multisource", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        provider = FakeModelProvider()
        deployment.models.register_provider(provider)
        model_id = "model-issue-650-multisource"
        deployment.models.register_model(
            ModelConfiguration(
                config_id=model_id,
                display_name="Issue 650 multisource model",
                provider_id=provider.descriptor.provider_id,
                capabilities=ModelCapabilities(context_window=65_536, modalities=("text",)),
                location=ModelLocation.LOCAL,
                health=HealthStatus.HEALTHY,
            )
        )
        admin = deployment.bootstrap_admin(
            "issue650-multisource-admin",
            "issue-650-multisource-password",
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        project = deployment.scopes.create_project(
            key="issue-650-multisource-project",
            name="Issue 650 multisource project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-650-multisource-workspace",
            project_id=project.id,
        )
        agent = deployment.agents.create_agent(
            AgentProfile(
                name="Issue 650 multisource worker",
                role="worker",
                instructions=AgentInstructions(
                    role=InstructionSource(
                        content="Use all authorized canonical Context sources.",
                        version="issue-650-multisource-v1",
                    )
                ),
            ),
            owner_ref=owner,
            project_id=project.id,
            workspace_id=workspace.id,
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue650-multisource-create",
            title="Multisource Context E2E",
            objective="Use Task, Plan, Agent, Skill, Research, Repository and File context.",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue650-multisource-ready",
            task_id=task.task_id,
        )
        planned = await deployment.kernel.plan_task(
            idempotency_key="issue650-multisource-plan",
            task_id=task.task_id,
            actor_ref=admin.user_id,
        )
        assert planned.plan_ref is not None and len(planned.step_ids) == 1
        plan_id, step_id = planned.plan_ref, planned.step_ids[0]
        plan = Plan(
            id=plan_id,
            task_id=task.task_id,
            owner_ref=owner,
            active=True,
            project_id=project.id,
            provenance=Provenance(source="issue-650-test", actor_ref=admin.user_id),
        )
        step = Step(
            id=step_id,
            plan_id=plan_id,
            title="Execute multisource Context",
            owner_ref=owner,
            project_id=project.id,
            provenance=Provenance(source="issue-650-test", actor_ref=admin.user_id),
        )
        deployment.coordination_repository.create_plan(
            plan,
            (step,),
            (
                StepCoordinationRecord(
                    task_id=task.task_id,
                    plan_id=plan_id,
                    plan_revision=1,
                    step_id=step_id,
                    phase=CoordinationPhase.READY,
                ),
            ),
        )

        file_id, artifact_id, result_id = new_id("file"), new_id("artifact"), new_id("result")
        await deployment.kernel.update_task(
            idempotency_key="issue650-multisource-agent-binding",
            task_id=task.task_id,
            metadata=encode_agent_step_execution_bindings(
                {
                    step_id: AgentExecutionBinding(
                        agent_id=agent.agent_id,
                        agent_revision=agent.revision,
                        model_config_id=model_id,
                        workspace_id=workspace.id,
                        input_refs=(file_id, artifact_id),
                        output_refs=(result_id,),
                    )
                }
            ),
        )
        queued = await deployment.kernel.create_run(
            idempotency_key="issue650-multisource-create-run",
            task_id=task.task_id,
            subject_type="step",
            subject_id=step_id,
            actor_ref=admin.user_id,
        )
        operation = OperationContext(
            correlation_id=task.task_id,
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        access = DataAccessContext(
            operation=operation,
            actor_ref=admin.user_id,
            task_id=task.task_id,
            run_id=queued.run_id,
            agent_id=agent.agent_id,
        )
        await deployment.files.create_file(
            b"durable issue 650 file context\n",
            access,
            file_id=file_id,
            content_type="text/plain",
        )
        await deployment.files.link_artifact(file_id, artifact_id, access)
        skill = SkillBundle(
            skill_bundle_id=new_id("skill_bundle"),
            digest=hashlib.sha256(b"issue-650-skill-bundle").hexdigest(),
            entries=(),
            resolver_version="issue-650-skill-resolver/v1",
            policy_version="issue-650-skill-policy/v1",
            run_id=queued.run_id,
            task_id=task.task_id,
            agent_id=agent.agent_id,
            agent_revision=agent.revision,
            step_id=step_id,
            project_id=project.id,
            workspace_id=workspace.id,
        )
        deployment.context.skills_repository.save_bundle(skill)

        actor = ActorIdentity(admin.user_id, ActorType.HUMAN)
        item = await deployment.context.research.create_item(
            title="Issue 650 evidence",
            question="Does exact Research evidence reach the Context Bundle?",
            research_class=ResearchClass.PROJECT_RESEARCH,
            owner_ref=owner,
            project_id=project.id,
            workspace_id=workspace.id,
            task_id=task.task_id,
            plan_id=plan_id,
            run_id=queued.run_id,
            actor=actor,
            operation=operation,
        )
        source = await deployment.context.research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.WEB,
            locator="https://example.test/issue-650",
            title="Issue 650 source",
            actor=actor,
            operation=operation,
        )
        observation = await deployment.context.research.observe_source(
            source.source_id,
            retrieved_at=datetime.now(UTC),
            revision="r1",
            content_digest=hashlib.sha256(b"research-content").hexdigest(),
            snapshot_digest=hashlib.sha256(b"research-snapshot").hexdigest(),
            identity_proven=True,
            actor=actor,
            operation=operation,
        )
        claim = await deployment.context.research.add_claim(
            item.research_item_id,
            text="Canonical Research evidence is available.",
            category="architecture",
            run_id=queued.run_id,
            actor=actor,
            operation=operation,
        )
        await deployment.context.research.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
            location_ref="section:issue-650",
            task_id=task.task_id,
            run_id=queued.run_id,
            agent_id=agent.agent_id,
            agent_revision=agent.revision,
            actor=actor,
            operation=operation,
        )
        deployment.context.research.assess_claim(claim.claim_id)
        deployment.repository_provenance.record(
            RepositoryRunProvenance(
                run_id=queued.run_id,
                repository_id=new_id("external_resource"),
                input_revision="a" * 40,
                actor_ref=admin.user_id,
                agent_id=agent.agent_id,
                branch_ref="refs/heads/main",
                task_id=task.task_id,
            )
        )

        started = await deployment.kernel.start_run(
            idempotency_key="issue650-multisource-start-run",
            task_id=task.task_id,
            run_id=queued.run_id,
            actor_ref=admin.user_id,
        )
        await deployment.kernel.refresh_run(
            idempotency_key="issue650-multisource-refresh",
            task_id=task.task_id,
            run_id=started.run_id,
            actor_ref=admin.user_id,
        )
        agent_runs = deployment.agents.repository.list_agent_runs(queued.run_id)
        assert len(agent_runs) == 1
        agent_run = agent_runs[0]
        binding = deployment.context.run_bindings.get(agent_run.agent_run_id)
        bundle = deployment.context.bundles.get(binding.context_bundle_id)
        assert {
            ContextSourceType.TASK,
            ContextSourceType.PLAN_STEP,
            ContextSourceType.AGENT,
            ContextSourceType.SKILL,
            ContextSourceType.RESEARCH_EVIDENCE,
            ContextSourceType.REPOSITORY,
            ContextSourceType.FILE,
            ContextSourceType.ARTIFACT,
            ContextSourceType.RESULT,
        }.issubset({entry.source.source_type for entry in bundle.entries})
        assert bundle.plan_id == plan_id and bundle.step_id == step_id
        assert bundle.skill_bundle_id == skill.skill_bundle_id
        assert binding.context_bundle_digest == bundle.digest
        assert agent_run.selected_model_config_id == model_id
        assert provider.calls

        request = RequestContext(
            request_id="request:issue650-multisource",
            correlation_id="correlation:issue650-multisource",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
                actor_type="human",
            ),
        )
        inspected = await deployment.control_plane.get_extension_resource(
            request,
            "context-bundles",
            bundle.context_bundle_id,
        )
        assert inspected["digest"] == bundle.digest
        assert inspected["run_id"] == queued.run_id

        restarted = build_single_node_deployment(config)
        assert restarted.context.bundles.get(bundle.context_bundle_id).digest == bundle.digest
        assert restarted.context.run_bindings.get(agent_run.agent_run_id) == binding
        assert restarted.context.reconciliation.already_bound >= 1

    asyncio.run(scenario())
