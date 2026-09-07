from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.governance import (
    ConversionStatus,
    GovernanceCallContext,
    GovernanceService,
    Proposal,
    ProposalStatus,
    SpecificationRevision,
    SqliteGovernanceRepository,
)
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    RiskClassification,
    SqliteApprovalService,
    infer_actor_identity,
)
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend

PRINCIPAL = "user:governance-test"
OWNER = OwnerRef(type="user", id="governance-test")


def _kernel(repository: SqliteKernelRepository | None = None) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )


def _gate(path: Path) -> AuthorizationGate:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=PRINCIPAL,
                actor_types=frozenset({ActorType.HUMAN}),
                administrator=True,
            ),
        )
    )
    return AuthorizationGate(provider, approvals=SqliteApprovalService(path))


def _service(tmp_path: Path) -> tuple[GovernanceService, AuthorizationGate]:
    gate = _gate(tmp_path / "approvals.sqlite3")
    service = GovernanceService(
        SqliteGovernanceRepository(tmp_path / "governance.sqlite3"),
        _kernel(),
        gate,
    )
    return service, gate


def _context(*, approval_id: str | None = None) -> GovernanceCallContext:
    return GovernanceCallContext(
        actor_ref=PRINCIPAL,
        correlation_id="governance-test",
        approval_id=approval_id,
    )


def _proposal(*, title: str = "Investigate flaky deployment") -> Proposal:
    return Proposal(
        title=title,
        summary="Deployment health signal crossed the investigation threshold.",
        reason="Repeated deployment failures require a reviewable change request.",
        owner_ref=OWNER,
        requester_ref=PRINCIPAL,
        source="test-signal",
        status=ProposalStatus.PROPOSED,
        evidence_refs=("event:test-signal",),
        confidence=0.9,
    )


def _specification(
    proposal: Proposal,
    *,
    risk: RiskClassification = RiskClassification.STANDARD,
    human_gates: tuple[str, ...] = (),
) -> SpecificationRevision:
    return SpecificationRevision(
        proposal_id=proposal.id,
        problem="Deployment intermittently fails during the release verification stage.",
        goal="Identify and correct the deterministic cause without changing unrelated runtime behavior.",
        scope=("release verification", "failure reproduction"),
        out_of_scope=("provider migration",),
        acceptance_criteria=("failure is reproduced", "targeted regression test passes"),
        constraints=("preserve canonical Task lifecycle",),
        required_tests=("targeted regression", "existing release suite"),
        verification_requirements=("record deterministic evidence",),
        decomposition_hints=("reproduce before modifying implementation",),
        required_human_gates=human_gates,
        owner_ref=OWNER,
        requester_ref=PRINCIPAL,
        risk=risk,
    )


async def _approve(gate: AuthorizationGate, approval_id: str) -> None:
    await gate.decide_approval(
        approval_id,
        approver=infer_actor_identity(PRINCIPAL),
        approve=True,
        operation=OperationContext(correlation_id="approve-governance-specification"),
        comment="reviewed exact specification revision",
    )


def test_direct_task_creation_remains_available_without_governance() -> None:
    task = asyncio.run(
        _kernel().create_task(
            idempotency_key="direct-task",
            title="Direct canonical Task",
            objective="Prove Proposal and Specification remain optional",
            owner_type="user",
            owner_id="governance-test",
        )
    )
    assert task.task.title == "Direct canonical Task"
    assert "governance" not in task.task.metadata


def test_proposal_specification_approval_and_task_conversion(tmp_path: Path) -> None:
    service, gate = _service(tmp_path)
    proposal = service.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = service.create_specification(
        _specification(
            proposal,
            risk=RiskClassification.HIGH,
            human_gates=("human review before execution",),
        ),
        actor_ref=PRINCIPAL,
    )

    with pytest.raises(ContractError) as denied:
        asyncio.run(service.convert_to_task(specification.id, context=_context()))
    assert denied.value.code is ErrorCode.FORBIDDEN

    pending = gate.approvals.pending_for(
        service._conversion_action(specification, PRINCIPAL, "governance-test")  # noqa: SLF001
    )
    assert pending is not None
    asyncio.run(_approve(gate, pending.approval_id))

    task = asyncio.run(
        service.convert_to_task(
            specification.id,
            context=_context(approval_id=pending.approval_id),
        )
    )
    metadata = task.task.metadata["governance"]
    assert isinstance(metadata, dict | object)
    assert metadata["proposal_id"] == proposal.id  # type: ignore[index]
    assert metadata["specification_id"] == specification.id  # type: ignore[index]
    assert metadata["specification_revision"] == 1  # type: ignore[index]
    assert metadata["specification_digest"] == specification.content_digest  # type: ignore[index]
    assert metadata["approval_id"] == pending.approval_id  # type: ignore[index]
    assert service.repository.get_proposal(proposal.id).status is ProposalStatus.CONVERTED_TO_TASK


def test_stale_approval_cannot_authorize_revised_specification(tmp_path: Path) -> None:
    service, gate = _service(tmp_path)
    proposal = service.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    first = service.create_specification(
        _specification(proposal, risk=RiskClassification.HIGH),
        actor_ref=PRINCIPAL,
    )
    approval = asyncio.run(service.request_approval(first.id, context=_context()))
    asyncio.run(_approve(gate, approval.approval_id))

    second = replace(
        first,
        revision=2,
        goal="Identify, correct, and document the deterministic release verification cause.",
        content_digest="",
        created_at=datetime.now(UTC),
    )
    second = service.revise_specification(
        second,
        expected_revision=1,
        actor_ref=PRINCIPAL,
    )
    assert second.content_digest != first.content_digest

    with pytest.raises(ContractError) as stale:
        asyncio.run(
            service.convert_to_task(
                second.id,
                context=_context(approval_id=approval.approval_id),
            )
        )
    assert stale.value.code is ErrorCode.FORBIDDEN
    assert any(
        event.event_type == "specification.stale-approval-rejected"
        for event in service.repository.list_audit()
    )


def test_duplicate_conversion_returns_exact_same_task(tmp_path: Path) -> None:
    service, _gate_value = _service(tmp_path)
    proposal = service.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = service.create_specification(_specification(proposal), actor_ref=PRINCIPAL)

    first = asyncio.run(service.convert_to_task(specification.id, context=_context()))
    second = asyncio.run(service.convert_to_task(specification.id, context=_context()))

    assert second.task_id == first.task_id
    conversion = service.repository.get_conversion(specification.id)
    assert conversion is not None
    assert conversion.status is ConversionStatus.COMPLETED
    assert conversion.task_id == first.task_id
    history = asyncio.run(service.kernel.history(first.task_id))
    assert [event.event_type for event in history].count("task.created") == 1


def test_restart_recovers_reserved_or_completed_conversion_without_duplicate_task(
    tmp_path: Path,
) -> None:
    governance_path = tmp_path / "governance.sqlite3"
    approval_path = tmp_path / "approvals.sqlite3"
    kernel_path = tmp_path / "kernel.sqlite3"

    kernel_repository = SqliteKernelRepository(kernel_path)
    first = GovernanceService(
        SqliteGovernanceRepository(governance_path),
        _kernel(kernel_repository),
        _gate(approval_path),
    )
    proposal = first.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = first.create_specification(_specification(proposal), actor_ref=PRINCIPAL)
    task = asyncio.run(first.convert_to_task(specification.id, context=_context()))

    restarted = GovernanceService(
        SqliteGovernanceRepository(governance_path),
        _kernel(SqliteKernelRepository(kernel_path)),
        _gate(approval_path),
    )
    recovered = asyncio.run(restarted.convert_to_task(specification.id, context=_context()))
    assert recovered.task_id == task.task_id
    assert [
        event.event_type for event in asyncio.run(restarted.kernel.history(task.task_id))
    ].count("task.created") == 1


def test_concurrent_specification_revision_uses_optimistic_conflict(tmp_path: Path) -> None:
    path = tmp_path / "governance.sqlite3"
    gate = _gate(tmp_path / "approvals.sqlite3")
    primary = GovernanceService(SqliteGovernanceRepository(path), _kernel(), gate)
    concurrent = GovernanceService(SqliteGovernanceRepository(path), _kernel(), gate)
    proposal = primary.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    first = primary.create_specification(_specification(proposal), actor_ref=PRINCIPAL)

    revision_a = replace(
        first,
        revision=2,
        goal="Revision A",
        content_digest="",
        created_at=datetime.now(UTC),
    )
    revision_b = replace(
        first,
        revision=2,
        goal="Revision B",
        content_digest="",
        created_at=datetime.now(UTC),
    )
    primary.revise_specification(revision_a, expected_revision=1, actor_ref=PRINCIPAL)
    with pytest.raises(ContractError) as conflict:
        concurrent.revise_specification(revision_b, expected_revision=1, actor_ref=PRINCIPAL)
    assert conflict.value.code is ErrorCode.CONFLICT


def test_proposal_supersession_preserves_lineage(tmp_path: Path) -> None:
    service, _gate_value = _service(tmp_path)
    original = service.create_proposal(_proposal(title="Original"), actor_ref=PRINCIPAL)
    replacement = replace(
        _proposal(title="Replacement"),
        supersedes_id=original.id,
    )
    old, new = service.supersede_proposal(
        original.id,
        replacement,
        expected_revision=1,
        actor_ref=PRINCIPAL,
    )
    assert old.status is ProposalStatus.SUPERSEDED
    assert old.superseded_by_id == new.id
    assert new.supersedes_id == old.id
    assert service.repository.get_proposal(original.id, 1).status is ProposalStatus.PROPOSED


def test_planner_consumes_exact_approved_revision_without_owning_it(tmp_path: Path) -> None:
    service, gate = _service(tmp_path)
    proposal = service.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = service.create_specification(
        _specification(
            proposal,
            risk=RiskClassification.CRITICAL,
            human_gates=("architecture review",),
        ),
        actor_ref=PRINCIPAL,
    )
    approval = asyncio.run(service.request_approval(specification.id, context=_context()))
    asyncio.run(_approve(gate, approval.approval_id))

    planning = service.planning_input(
        specification.id,
        1,
        actor_ref=PRINCIPAL,
        correlation_id="governance-test",
    )
    assert planning.specification_id == specification.id
    assert planning.revision == 1
    assert planning.content_digest == specification.content_digest
    assert planning.required_tests == specification.required_tests
