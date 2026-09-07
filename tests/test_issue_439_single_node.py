from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionRequest,
    OperationContext,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.planning import ProposalStatus
from ai_multi_agent_platform.planning.composition import PlanningOnlyLifecycleBackend
from ai_multi_agent_platform.security import ActorIdentity, ActorType


def _profile() -> AgentProfile:
    return AgentProfile(
        name="Single-node planning worker",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(
                content="Execute work only after canonical planning activation.",
                version="1",
            )
        ),
    )


def test_planning_only_lifecycle_rejects_every_execution_operation() -> None:
    async def scenario() -> None:
        backend = PlanningOnlyLifecycleBackend()
        task_id = new_id("task")
        run_id = new_id("run")
        context = OperationContext(correlation_id=task_id)
        request = ExecutionRequest(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            context=context,
        )

        with pytest.raises(ContractError) as start_error:
            await backend.start(request)
        with pytest.raises(ContractError) as get_error:
            await backend.get(run_id, context)
        with pytest.raises(ContractError) as cancel_error:
            await backend.cancel(run_id, context)

        assert start_error.value.code is ErrorCode.FORBIDDEN
        assert get_error.value.code is ErrorCode.FORBIDDEN
        assert cancel_error.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_public_single_node_composes_planning_and_hands_activation_to_coordinator(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin(
            "planning-admin",
            "correct horse battery staple for planning",
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        agent = deployment.agents.create_agent(_profile(), owner_ref=owner)

        created = await deployment.kernel.create_task(
            idempotency_key="issue-439:single-node:create",
            title="Autonomously plan this goal",
            objective="Produce a canonical plan and hand execution to the coordinator",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue-439:single-node:ready",
            task_id=created.task_id,
        )

        proposal = await deployment.planning.propose(
            task_id=created.task_id,
            idempotency_key="issue-439:single-node:propose",
        )
        assert proposal.status is ProposalStatus.VALIDATED
        assert proposal.proposal.steps[0].assignment is not None
        assert proposal.proposal.steps[0].assignment.agent_id == agent.agent_id

        activated = await deployment.planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key="issue-439:single-node:activate",
            actor=ActorIdentity(actor_id=admin.user_id, actor_type=ActorType.HUMAN),
        )
        assert activated.status is ProposalStatus.ACTIVATED
        assert activated.activation_plan_id is not None
        assert deployment.coordination.projection(activated.activation_plan_id).plan_id == (
            activated.activation_plan_id
        )

        assert "planning-proposals" in deployment.control_plane.registered_collections
        assert "planning.propose" in deployment.control_plane.registered_commands
        assert "planning.activate" in deployment.control_plane.registered_commands
        assert "planning.reject" in deployment.control_plane.registered_commands

        history = await deployment.kernel.history(created.task_id)
        planning_plan_events = []
        for event in history:
            if event.event_type != "plan.created":
                continue
            adapter_metadata = event.payload.get("adapter_metadata")
            if not isinstance(adapter_metadata, Mapping):
                continue
            planning_metadata = adapter_metadata.get("platform-planning")
            if (
                isinstance(planning_metadata, Mapping)
                and planning_metadata.get("proposal_id") == proposal.proposal.proposal_id
            ):
                planning_plan_events.append(event)
        assert len(planning_plan_events) == 1
        assert any(
            entry.event_name == "planning.revision.activated"
            and entry.context.task_id == created.task_id
            for entry in deployment.observability_exporter.timeline
        )

    asyncio.run(scenario())


def test_public_single_node_recovers_unactivated_proposal_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "restart", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin(
            "planning-restart-admin",
            "correct horse battery staple for planning restart",
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        first.agents.create_agent(_profile(), owner_ref=owner)
        task = await first.kernel.create_task(
            idempotency_key="issue-439:restart:create",
            title="Persist proposal",
            objective="Keep a validated planning proposal durable across process restart",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await first.kernel.ready_task(
            idempotency_key="issue-439:restart:ready",
            task_id=task.task_id,
        )
        proposal = await first.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-439:restart:propose",
        )
        proposal_id = proposal.proposal.proposal_id

        restarted = build_single_node_deployment(config)
        restored = restarted.planning_repository.get(proposal_id)
        assert restored == proposal
        assert restored.status is ProposalStatus.VALIDATED
        assert restarted.planning.repository is restarted.planning_repository

    asyncio.run(scenario())
