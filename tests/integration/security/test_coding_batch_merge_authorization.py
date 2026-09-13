"""Migrated under #722; original coverage tracked issue #872."""

from __future__ import annotations

import pytest

from ai_multi_agent_platform.coding_batches import (
    AuthorizedCodingBatchIntegration,
    CheckState,
    CodingBatchAuthorizationContext,
    CodingBatchCoordinator,
    CodingWorkItem,
    CombinedValidationEvidence,
    IntegrationExecutionProvenance,
    IntegrationState,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamResult,
)
from ai_multi_agent_platform.coding_batches.service import (
    CodingBatchCoordinator as StateCoordinator,
)
from ai_multi_agent_platform.contracts import ContractError, OperationContext
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)

BASE = "a" * 40
OUTPUT = "b" * 40
INTEGRATED = "c" * 40
ACTOR_REF = "human:integration-reviewer"


def _validated_candidate() -> tuple[CodingBatchCoordinator, str, str]:
    coordinator = CodingBatchCoordinator()
    batch = coordinator.create_batch(
        request_key="request-872-authorization",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(
            CodingWorkItem(
                work_item_id="a",
                task_id="task-a",
                plan_id="plan-a",
                step_id="step-a",
                affected_paths=("src/a.py",),
            ),
        ),
    )
    coordinator.materialize_workstream(
        batch.batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-a",
        agent_run_id="agent-run-a",
    )
    coordinator.start_workstream(batch.batch_id, "a")
    coordinator.record_result(
        batch.batch_id,
        "a",
        WorkstreamResult(OUTPUT, ("src/a.py",), "digest-a"),
    )
    coordinator.record_verification(
        batch.batch_id,
        "a",
        VerificationEvidence("verification-a", OUTPUT, True),
    )
    coordinator.accept_workstream(batch.batch_id, "a")
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    coordinator.bind_integration_execution(
        batch.batch_id,
        candidate.integration_id,
        IntegrationExecutionProvenance(
            task_id="task-integration",
            plan_id="plan-integration",
            plan_revision=1,
            step_id="step-integration",
            run_id="run-integration",
            agent_revision="integration-agent@1",
            agent_run_id="agent-run-integration",
            workspace_id="workspace-integration",
            snapshot_id="snapshot-integration",
            branch_ref="coding/integration-authorization",
        ),
    )
    coordinator.record_integrated_revision(
        batch.batch_id,
        candidate.integration_id,
        integrated_revision=INTEGRATED,
    )
    coordinator.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=INTEGRATED,
            verification_id="verification-combined",
            tests_passed=True,
            required_checks=(RequiredCheck("test", INTEGRATED, CheckState.PASS),),
        ),
    )
    return coordinator, batch.batch_id, candidate.integration_id


def _context() -> CodingBatchAuthorizationContext:
    return CodingBatchAuthorizationContext(
        actor=ActorIdentity(ACTOR_REF, ActorType.HUMAN),
        operation=OperationContext(
            correlation_id="issue-872-authorization",
            owner_type="user",
            owner_id="integration-reviewer",
        ),
    )


def _gate(*, approval_required: bool) -> AuthorizationGate:
    action_set = frozenset({AuthorizationAction.MODIFY})
    policy = LocalPrincipalPolicy(
        principal_ref=ACTOR_REF,
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset() if approval_required else action_set,
        approval_actions=action_set if approval_required else frozenset(),
        resource_types=frozenset({ResourceType.GENERIC}),
    )
    return AuthorizationGate(LocalAuthorizationProvider((policy,)))


def test_internal_state_machine_has_no_caller_asserted_authorization_seam() -> None:
    assert not hasattr(StateCoordinator, "mark_merge_ready")


@pytest.mark.asyncio
async def test_merge_readiness_is_bound_to_exact_canonical_authorization_action() -> None:
    coordinator, batch_id, integration_id = _validated_candidate()
    gate = _gate(approval_required=False)
    integration = AuthorizedCodingBatchIntegration(coordinator, gate)
    context = _context()
    batch = coordinator.get(batch_id)
    candidate = batch.integration_candidate(integration_id)
    action = integration.merge_ready_action(batch, candidate, context)

    ready = await integration.mark_merge_ready(
        batch_id,
        integration_id,
        context=context,
    )

    assert ready.state is IntegrationState.MERGE_READY
    assert gate.audit_records[-1].requested_action_digest == action.digest
    assert action.payload == {
        "batch_id": batch_id,
        "repository_id": "repo-platform",
        "target_ref": "main",
        "target_base_revision": BASE,
        "integration_id": integration_id,
        "ordered_workstream_ids": ["a"],
        "ordered_revisions": [OUTPUT],
        "integrated_revision": INTEGRATED,
        "verification_id": "verification-combined",
    }


@pytest.mark.asyncio
async def test_required_approval_blocks_without_mutating_merge_readiness() -> None:
    coordinator, batch_id, integration_id = _validated_candidate()
    gate = _gate(approval_required=True)
    integration = AuthorizedCodingBatchIntegration(coordinator, gate)
    context = _context()
    batch = coordinator.get(batch_id)
    candidate = batch.integration_candidate(integration_id)
    action = integration.merge_ready_action(batch, candidate, context)

    with pytest.raises(ContractError):
        await integration.mark_merge_ready(
            batch_id,
            integration_id,
            context=context,
        )

    unchanged = coordinator.get(batch_id).integration_candidate(integration_id)
    assert unchanged.state is IntegrationState.VALIDATED
    pending = gate.approvals.pending_for(action)
    assert pending is not None
    assert pending.requested_action_digest == action.digest
