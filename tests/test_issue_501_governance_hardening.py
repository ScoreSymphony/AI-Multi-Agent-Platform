from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.governance import (
    GovernanceService,
    Proposal,
    ProposalResourceService,
    ProposalStatus,
    SpecificationResourceService,
    SpecificationRevision,
    SqliteGovernanceRepository,
    proposal_resource,
    specification_resource,
)
from ai_multi_agent_platform.governance.control_plane import GovernanceAuditResourceService
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    SqliteApprovalService,
)
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend

PRINCIPAL = "user:governance-hardening"
OWNER = OwnerRef(type="user", id="governance-hardening")


def _kernel() -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
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


def _service(tmp_path: Path, *, repository_name: str = "governance.sqlite3") -> GovernanceService:
    return GovernanceService(
        SqliteGovernanceRepository(tmp_path / repository_name),
        _kernel(),
        _gate(tmp_path / "approvals.sqlite3"),
    )


def _proposal(project_id: str, *, title: str) -> Proposal:
    return Proposal(
        title=title,
        summary=f"Summary for {title}",
        reason="Reviewable governance intake",
        owner_ref=OWNER,
        requester_ref=PRINCIPAL,
        source="hardening-test",
        status=ProposalStatus.PROPOSED,
        project_id=project_id,
        evidence_refs=(f"evidence:{title}",),
    )


def _specification(proposal: Proposal) -> SpecificationRevision:
    return SpecificationRevision(
        proposal_id=proposal.id,
        project_id=proposal.project_id,
        problem="A scoped problem requires review.",
        goal=f"Resolve {proposal.title}",
        scope=("governance",),
        acceptance_criteria=("result is deterministic",),
        constraints=("preserve project isolation",),
        required_tests=("authorization regression",),
        owner_ref=OWNER,
        requester_ref=PRINCIPAL,
    )


def _request() -> RequestContext:
    return RequestContext(
        request_id="request-governance-hardening",
        correlation_id="correlation-governance-hardening",
        actor=ActorContext(
            principal_ref=PRINCIPAL,
            owner_type="user",
            owner_id=OWNER.id,
            actor_type="human",
        ),
    )


class _ProjectScopedControlPlane:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        **scope: object,
    ) -> bool:
        del context, action, resource_ref
        return scope.get("project_id") == self.project_id


def test_restart_preserves_proposal_and_exact_specification_revision_history(
    tmp_path: Path,
) -> None:
    project_id = new_id("project")
    first = _service(tmp_path)
    proposal = first.create_proposal(
        _proposal(project_id, title="Restart persistence"),
        actor_ref=PRINCIPAL,
    )
    specification = first.create_specification(
        _specification(proposal),
        actor_ref=PRINCIPAL,
    )
    revised = first.revise_specification(
        replace(
            specification,
            revision=2,
            goal="Resolve restart persistence with exact durable revision history",
            content_digest="",
            created_at=datetime.now(UTC),
        ),
        expected_revision=1,
        actor_ref=PRINCIPAL,
    )

    restarted = _service(tmp_path)
    persisted_proposal = restarted.repository.get_proposal(proposal.id)
    persisted_specification = restarted.repository.get_specification(specification.id)

    assert persisted_proposal.id == proposal.id
    assert persisted_specification.revision == 2
    assert persisted_specification.content_digest == revised.content_digest
    assert [
        value.revision for value in restarted.repository.specification_history(specification.id)
    ] == [
        1,
        2,
    ]


def test_generic_revision_cannot_forge_terminal_proposal_state(tmp_path: Path) -> None:
    project_id = new_id("project")
    service = _service(tmp_path)
    proposal = service.create_proposal(
        _proposal(project_id, title="Lifecycle invariant"),
        actor_ref=PRINCIPAL,
    )

    forged = replace(
        proposal,
        status=ProposalStatus.DISMISSED,
        revision=2,
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ContractError) as error:
        service.revise_proposal(forged, expected_revision=1, actor_ref=PRINCIPAL)
    assert error.value.code is ErrorCode.INVALID_REQUEST

    dismissed = service.dismiss_proposal(
        proposal.id,
        expected_revision=1,
        actor_ref=PRINCIPAL,
    )
    assert dismissed.status is ProposalStatus.DISMISSED


def test_control_plane_search_and_audit_reads_are_project_isolated(tmp_path: Path) -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    service = _service(tmp_path)
    proposal_a = service.create_proposal(
        _proposal(project_a, title="Project A"), actor_ref=PRINCIPAL
    )
    proposal_b = service.create_proposal(
        _proposal(project_b, title="Project B"), actor_ref=PRINCIPAL
    )
    specification_a = service.create_specification(_specification(proposal_a), actor_ref=PRINCIPAL)
    specification_b = service.create_specification(_specification(proposal_b), actor_ref=PRINCIPAL)

    scoped = cast(Any, _ProjectScopedControlPlane(project_a))
    proposal_api = ProposalResourceService(scoped, service)
    specification_api = SpecificationResourceService(scoped, service)
    audit_api = GovernanceAuditResourceService(scoped, service)
    context = _request()

    proposals = asyncio.run(proposal_api.list_resources(context, PageQuery()))
    specifications = asyncio.run(specification_api.list_resources(context, PageQuery()))
    assert {value["id"] for value in proposals} == {proposal_a.id}
    assert {value["id"] for value in specifications} == {specification_a.id}

    with pytest.raises(ContractError) as hidden_proposal:
        asyncio.run(proposal_api.get_resource(context, proposal_b.id))
    assert hidden_proposal.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(ContractError) as hidden_specification:
        asyncio.run(specification_api.get_resource(context, specification_b.id))
    assert hidden_specification.value.code is ErrorCode.FORBIDDEN

    search_candidates = asyncio.run(proposal_api.list_search_resources())
    assert {value["id"] for value in search_candidates} == {proposal_a.id, proposal_b.id}
    assert asyncio.run(proposal_api.search_result_allowed(context, proposal_a.id)) is True
    assert asyncio.run(proposal_api.search_result_allowed(context, proposal_b.id)) is False
    assert asyncio.run(specification_api.search_result_allowed(context, specification_a.id)) is True
    assert (
        asyncio.run(specification_api.search_result_allowed(context, specification_b.id)) is False
    )

    audit_events = asyncio.run(audit_api.list_resources(context, PageQuery()))
    assert audit_events
    assert {value["project_id"] for value in audit_events} == {project_a}
    project_b_event = next(
        event for event in service.repository.list_audit() if event.project_id == project_b
    )
    with pytest.raises(ContractError) as hidden_audit:
        asyncio.run(audit_api.get_resource(context, project_b_event.id))
    assert hidden_audit.value.code is ErrorCode.FORBIDDEN


def test_api_and_search_projections_are_json_safe_and_search_minimal(tmp_path: Path) -> None:
    project_id = new_id("project")
    service = _service(tmp_path)
    proposal = service.create_proposal(
        _proposal(project_id, title="Projection contract"), actor_ref=PRINCIPAL
    )
    specification = service.create_specification(_specification(proposal), actor_ref=PRINCIPAL)
    scoped = cast(Any, _ProjectScopedControlPlane(project_id))
    proposal_api = ProposalResourceService(scoped, service)
    specification_api = SpecificationResourceService(scoped, service)

    full_proposal = proposal_resource(proposal)
    full_specification = specification_resource(specification)
    proposal_search = asyncio.run(proposal_api.list_search_resources())[0]
    specification_search = asyncio.run(specification_api.list_search_resources())[0]

    json.dumps(full_proposal, allow_nan=False)
    json.dumps(full_specification, allow_nan=False)
    json.dumps(proposal_search, allow_nan=False)
    json.dumps(specification_search, allow_nan=False)

    assert "reason" in full_proposal and "reason" not in proposal_search
    assert "evidence_refs" in full_proposal and "evidence_refs" not in proposal_search
    assert "problem" in full_specification and "problem" not in specification_search
    assert "acceptance_criteria" in full_specification
    assert "acceptance_criteria" not in specification_search


def test_baseline_governance_requires_no_external_spec_adapter(tmp_path: Path) -> None:
    project_id = new_id("project")
    service = _service(tmp_path)
    proposal = service.create_proposal(
        _proposal(project_id, title="No external adapter"), actor_ref=PRINCIPAL
    )
    specification = service.create_specification(_specification(proposal), actor_ref=PRINCIPAL)

    assert proposal.id.startswith("proposal_")
    assert specification.id.startswith("specification_")
    assert specification.content_digest
