from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

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
from ai_multi_agent_platform.governance.repository import GovernanceRepository
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

PRINCIPAL = "user:governance-followup"
OWNER = OwnerRef(type="user", id="governance-followup")


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


def _context(*, approval_id: str | None = None) -> GovernanceCallContext:
    return GovernanceCallContext(
        actor_ref=PRINCIPAL,
        correlation_id="governance-followup",
        approval_id=approval_id,
    )


def _proposal(*, title: str = "Governed change") -> Proposal:
    return Proposal(
        title=title,
        summary="A reviewable change is required.",
        reason="Exercise the Proposal/Specification conversion contract.",
        owner_ref=OWNER,
        requester_ref=PRINCIPAL,
        source="issue-501-followup",
        status=ProposalStatus.PROPOSED,
    )


def _specification(
    proposal: Proposal,
    *,
    risk: RiskClassification = RiskClassification.STANDARD,
) -> SpecificationRevision:
    return SpecificationRevision(
        proposal_id=proposal.id,
        problem="A deterministic governance regression must be prevented.",
        goal="Preserve exact durable Proposal/Specification conversion semantics.",
        scope=("governance conversion",),
        acceptance_criteria=("conversion is deterministic",),
        constraints=("no duplicate Task",),
        required_tests=("issue 501 recovery regression",),
        owner_ref=OWNER,
        requester_ref=PRINCIPAL,
        risk=risk,
    )


async def _approve(gate: AuthorizationGate, approval_id: str) -> None:
    await gate.decide_approval(
        approval_id,
        approver=infer_actor_identity(PRINCIPAL),
        approve=True,
        operation=OperationContext(correlation_id="approve-issue-501-followup"),
        comment="approved exact specification revision",
    )


def _service(
    governance_path: Path,
    approval_path: Path,
    *,
    kernel: PlatformKernel | None = None,
) -> tuple[GovernanceService, AuthorizationGate]:
    gate = _gate(approval_path)
    return (
        GovernanceService(
            SqliteGovernanceRepository(governance_path),
            kernel or _kernel(),
            gate,
        ),
        gate,
    )


class _CrashAfterReserveRepository:
    def __init__(self, delegate: SqliteGovernanceRepository) -> None:
        self.delegate = delegate
        self.fail_once = True

    def reserve_conversion(self, conversion: Any) -> Any:
        reserved = self.delegate.reserve_conversion(conversion)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated crash after conversion reservation")
        return reserved

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


class _CrashBeforeProvenanceKernel:
    def __init__(self, delegate: PlatformKernel) -> None:
        self.delegate = delegate
        self.fail_once = True

    async def create_task(self, **kwargs: Any) -> Any:
        return await self.delegate.create_task(**kwargs)

    async def update_task(self, **kwargs: Any) -> Any:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated crash after canonical Task creation")
        return await self.delegate.update_task(**kwargs)

    async def get_task(self, task_id: str) -> Any:
        return await self.delegate.get_task(task_id)


class _CrashBeforeCompleteRepository:
    def __init__(self, delegate: SqliteGovernanceRepository) -> None:
        self.delegate = delegate
        self.fail_once = True

    def complete_conversion(self, specification_id: str, *, approval_id: str | None) -> Any:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated crash after provenance update")
        return self.delegate.complete_conversion(specification_id, approval_id=approval_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


def test_dismissed_or_superseded_proposal_cannot_start_conversion(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path / "governance.sqlite3",
        tmp_path / "approvals.sqlite3",
    )

    dismissed = service.create_proposal(_proposal(title="Dismissed"), actor_ref=PRINCIPAL)
    dismissed_spec = service.create_specification(_specification(dismissed), actor_ref=PRINCIPAL)
    dismissed_current = service.repository.get_proposal(dismissed.id)
    service.dismiss_proposal(
        dismissed.id,
        expected_revision=dismissed_current.revision,
        actor_ref=PRINCIPAL,
    )
    with pytest.raises(ContractError) as dismissed_error:
        asyncio.run(service.convert_to_task(dismissed_spec.id, context=_context()))
    assert dismissed_error.value.code is ErrorCode.CONFLICT
    assert service.repository.get_conversion(dismissed_spec.id) is None

    superseded = service.create_proposal(_proposal(title="Superseded"), actor_ref=PRINCIPAL)
    superseded_spec = service.create_specification(_specification(superseded), actor_ref=PRINCIPAL)
    superseded_current = service.repository.get_proposal(superseded.id)
    replacement = replace(_proposal(title="Replacement"), supersedes_id=superseded.id)
    service.supersede_proposal(
        superseded.id,
        replacement,
        expected_revision=superseded_current.revision,
        actor_ref=PRINCIPAL,
    )
    with pytest.raises(ContractError) as superseded_error:
        asyncio.run(service.convert_to_task(superseded_spec.id, context=_context()))
    assert superseded_error.value.code is ErrorCode.CONFLICT
    assert service.repository.get_conversion(superseded_spec.id) is None


def test_terminal_proposal_state_machine_rejects_cross_terminal_transitions(
    tmp_path: Path,
) -> None:
    service, _ = _service(
        tmp_path / "governance.sqlite3",
        tmp_path / "approvals.sqlite3",
    )

    dismissed = service.create_proposal(_proposal(title="Dismiss terminal"), actor_ref=PRINCIPAL)
    dismissed = service.dismiss_proposal(
        dismissed.id,
        expected_revision=dismissed.revision,
        actor_ref=PRINCIPAL,
    )
    before_ids = {proposal.id for proposal in service.repository.list_proposals()}
    with pytest.raises(ContractError) as supersede_error:
        service.supersede_proposal(
            dismissed.id,
            replace(_proposal(title="Must not be created"), supersedes_id=dismissed.id),
            expected_revision=dismissed.revision,
            actor_ref=PRINCIPAL,
        )
    assert supersede_error.value.code is ErrorCode.CONFLICT
    assert {proposal.id for proposal in service.repository.list_proposals()} == before_ids

    original = service.create_proposal(_proposal(title="Supersede terminal"), actor_ref=PRINCIPAL)
    superseded, _replacement = service.supersede_proposal(
        original.id,
        replace(_proposal(title="Valid replacement"), supersedes_id=original.id),
        expected_revision=original.revision,
        actor_ref=PRINCIPAL,
    )
    with pytest.raises(ContractError) as dismiss_error:
        service.dismiss_proposal(
            superseded.id,
            expected_revision=superseded.revision,
            actor_ref=PRINCIPAL,
        )
    assert dismiss_error.value.code is ErrorCode.CONFLICT


def test_restart_between_approval_and_conversion_uses_persisted_approval(
    tmp_path: Path,
) -> None:
    governance_path = tmp_path / "governance.sqlite3"
    approval_path = tmp_path / "approvals.sqlite3"
    kernel_path = tmp_path / "kernel.sqlite3"

    first, gate = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    proposal = first.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = first.create_specification(
        _specification(proposal, risk=RiskClassification.HIGH),
        actor_ref=PRINCIPAL,
    )
    approval = asyncio.run(first.request_approval(specification.id, context=_context()))
    asyncio.run(_approve(gate, approval.approval_id))

    restarted, _ = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    task = asyncio.run(
        restarted.convert_to_task(
            specification.id,
            context=_context(approval_id=approval.approval_id),
        )
    )

    assert task.task.metadata["governance"]["approval_id"] == approval.approval_id  # type: ignore[index]
    assert [
        event.event_type for event in asyncio.run(restarted.kernel.history(task.task_id))
    ].count("task.created") == 1


def test_completed_high_risk_conversion_replays_after_approval_expiry_and_restart(
    tmp_path: Path,
) -> None:
    governance_path = tmp_path / "governance.sqlite3"
    approval_path = tmp_path / "approvals.sqlite3"
    kernel_path = tmp_path / "kernel.sqlite3"

    first, gate = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    proposal = first.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = first.create_specification(
        _specification(proposal, risk=RiskClassification.HIGH),
        actor_ref=PRINCIPAL,
    )
    approval = asyncio.run(first.request_approval(specification.id, context=_context()))
    asyncio.run(_approve(gate, approval.approval_id))
    task = asyncio.run(
        first.convert_to_task(
            specification.id,
            context=_context(approval_id=approval.approval_id),
        )
    )

    with sqlite3.connect(approval_path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM approvals WHERE approval_id = ?",
            (approval.approval_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[0]))
        created_at = datetime.fromisoformat(str(payload["approval"]["created_at"]))
        payload["expires_at"] = (created_at + timedelta(microseconds=1)).isoformat()
        connection.execute(
            "UPDATE approvals SET payload_json = ? WHERE approval_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), approval.approval_id),
        )

    restarted, restarted_gate = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    action = restarted._conversion_action(  # noqa: SLF001
        specification,
        PRINCIPAL,
        "governance-followup",
    )
    assert restarted_gate.approvals.valid_for(approval.approval_id, action) is False

    replayed = asyncio.run(
        restarted.convert_to_task(
            specification.id,
            context=_context(approval_id=approval.approval_id),
        )
    )
    assert replayed.task_id == task.task_id
    assert [
        event.event_type for event in asyncio.run(restarted.kernel.history(task.task_id))
    ].count("task.created") == 1
    assert any(
        event.event_type == "specification.conversion-replayed"
        for event in restarted.repository.list_audit()
    )


def test_reservation_survives_crash_and_blocks_terminal_proposal_transition(
    tmp_path: Path,
) -> None:
    governance_path = tmp_path / "governance.sqlite3"
    approval_path = tmp_path / "approvals.sqlite3"
    kernel_path = tmp_path / "kernel.sqlite3"
    durable_repository = SqliteGovernanceRepository(governance_path)
    crash_repository = _CrashAfterReserveRepository(durable_repository)
    first = GovernanceService(
        cast(GovernanceRepository, crash_repository),
        _kernel(SqliteKernelRepository(kernel_path)),
        _gate(approval_path),
    )
    proposal = first.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = first.create_specification(_specification(proposal), actor_ref=PRINCIPAL)

    with pytest.raises(RuntimeError, match="after conversion reservation"):
        asyncio.run(first.convert_to_task(specification.id, context=_context()))

    reserved = durable_repository.get_conversion(specification.id)
    assert reserved is not None
    assert reserved.status is ConversionStatus.RESERVED
    current = durable_repository.get_proposal(proposal.id)
    with pytest.raises(ContractError) as terminal_error:
        first.dismiss_proposal(
            proposal.id,
            expected_revision=current.revision,
            actor_ref=PRINCIPAL,
        )
    assert terminal_error.value.code is ErrorCode.CONFLICT

    restarted, _ = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    recovered = asyncio.run(restarted.convert_to_task(specification.id, context=_context()))
    assert recovered.task_id == reserved.task_id
    assert [
        event.event_type for event in asyncio.run(restarted.kernel.history(recovered.task_id))
    ].count("task.created") == 1


def test_task_creation_crash_retries_same_reserved_task_without_duplicate_event(
    tmp_path: Path,
) -> None:
    governance_path = tmp_path / "governance.sqlite3"
    approval_path = tmp_path / "approvals.sqlite3"
    kernel_path = tmp_path / "kernel.sqlite3"
    durable_kernel = _kernel(SqliteKernelRepository(kernel_path))
    crash_kernel = _CrashBeforeProvenanceKernel(durable_kernel)
    first, _ = _service(
        governance_path,
        approval_path,
        kernel=cast(PlatformKernel, crash_kernel),
    )
    proposal = first.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = first.create_specification(_specification(proposal), actor_ref=PRINCIPAL)

    with pytest.raises(RuntimeError, match="after canonical Task creation"):
        asyncio.run(first.convert_to_task(specification.id, context=_context()))

    reserved = first.repository.get_conversion(specification.id)
    assert reserved is not None
    assert reserved.status is ConversionStatus.RESERVED

    restarted, _ = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    recovered = asyncio.run(restarted.convert_to_task(specification.id, context=_context()))
    assert recovered.task_id == reserved.task_id
    assert recovered.task.metadata["governance"]["specification_id"] == specification.id  # type: ignore[index]
    assert [
        event.event_type for event in asyncio.run(restarted.kernel.history(recovered.task_id))
    ].count("task.created") == 1


def test_provenance_crash_retries_without_duplicate_task(tmp_path: Path) -> None:
    governance_path = tmp_path / "governance.sqlite3"
    approval_path = tmp_path / "approvals.sqlite3"
    kernel_path = tmp_path / "kernel.sqlite3"
    durable_repository = SqliteGovernanceRepository(governance_path)
    crash_repository = _CrashBeforeCompleteRepository(durable_repository)
    first = GovernanceService(
        cast(GovernanceRepository, crash_repository),
        _kernel(SqliteKernelRepository(kernel_path)),
        _gate(approval_path),
    )
    proposal = first.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    specification = first.create_specification(_specification(proposal), actor_ref=PRINCIPAL)

    with pytest.raises(RuntimeError, match="after provenance update"):
        asyncio.run(first.convert_to_task(specification.id, context=_context()))

    reserved = durable_repository.get_conversion(specification.id)
    assert reserved is not None
    assert reserved.status is ConversionStatus.RESERVED

    restarted, _ = _service(
        governance_path,
        approval_path,
        kernel=_kernel(SqliteKernelRepository(kernel_path)),
    )
    recovered = asyncio.run(restarted.convert_to_task(specification.id, context=_context()))
    assert recovered.task_id == reserved.task_id
    assert [
        event.event_type for event in asyncio.run(restarted.kernel.history(recovered.task_id))
    ].count("task.created") == 1


def test_material_revision_audits_approval_binding_invalidation(tmp_path: Path) -> None:
    service, gate = _service(
        tmp_path / "governance.sqlite3",
        tmp_path / "approvals.sqlite3",
    )
    proposal = service.create_proposal(_proposal(), actor_ref=PRINCIPAL)
    first = service.create_specification(
        _specification(proposal, risk=RiskClassification.HIGH),
        actor_ref=PRINCIPAL,
    )
    approval = asyncio.run(service.request_approval(first.id, context=_context()))
    asyncio.run(_approve(gate, approval.approval_id))

    second = service.revise_specification(
        replace(
            first,
            revision=2,
            goal=(
                "Preserve exact durable governance semantics and audit invalidated "
                "approval binding."
            ),
            content_digest="",
            created_at=datetime.now(UTC),
        ),
        expected_revision=1,
        actor_ref=PRINCIPAL,
    )

    invalidation = next(
        event
        for event in service.repository.list_audit()
        if event.event_type == "specification.approval-binding-invalidated"
    )
    assert invalidation.revision == second.revision
    assert invalidation.digest == second.content_digest
    assert invalidation.metadata["previous_revision"] == first.revision
    assert invalidation.metadata["previous_digest"] == first.content_digest
