from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest, CapabilitySpec
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentContent,
    CapabilityAssignmentRule,
    CapabilityAssignmentService,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
    InMemoryCapabilityAssignmentRepository,
    JsonCapabilityAssignmentRepository,
    ResolvedCapabilityAssignmentTarget,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, ProposedAction, RiskClassification

OWNER = OwnerRef(type="user", id="issue-366-hardening-owner")


@dataclass(slots=True)
class _Inventory:
    specs: tuple[CapabilitySpec, ...]

    def inventory_capabilities(
        self,
        *,
        include_unavailable: bool = True,
    ) -> tuple[CapabilitySpec, ...]:
        del include_unavailable
        return self.specs


@dataclass(slots=True)
class _Targets:
    target: CapabilityAssignmentTarget
    resolved: ResolvedCapabilityAssignmentTarget

    def resolve(
        self,
        target: CapabilityAssignmentTarget,
    ) -> ResolvedCapabilityAssignmentTarget:
        if target != self.target:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical assignment target not found")
        return self.resolved


class _AllowGate:
    async def decide(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision:
        del action, approval_id, risk
        return AuthorizationDecision(AuthorizationOutcome.ALLOW)

    async def enforce(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision:
        return await self.decide(action, approval_id=approval_id, risk=risk)


def _access() -> CapabilityAssignmentAccessContext:
    return CapabilityAssignmentAccessContext(
        actor=ActorIdentity(actor_id="user:issue-366", actor_type=ActorType.HUMAN),
        operation=OperationContext(correlation_id="issue-366-hardening"),
    )


def _target() -> CapabilityAssignmentTarget:
    return CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )


def _service(
    target: CapabilityAssignmentTarget,
    *,
    repository: InMemoryCapabilityAssignmentRepository | JsonCapabilityAssignmentRepository,
    specs: tuple[CapabilitySpec, ...],
    resolved: ResolvedCapabilityAssignmentTarget | None = None,
) -> CapabilityAssignmentService:
    return CapabilityAssignmentService(
        repository=repository,
        capabilities=_Inventory(specs),
        targets=_Targets(target, resolved or ResolvedCapabilityAssignmentTarget()),
        authorization=_AllowGate(),
    )


def test_create_inherits_canonical_target_scope_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "capability-assignments.json"
    target = _target()
    project_id = new_id("project")
    repository = JsonCapabilityAssignmentRepository(path)
    service = _service(
        target,
        repository=repository,
        specs=(CapabilitySpec(capability_id="tool.echo", name="Echo", version="1.0"),),
        resolved=ResolvedCapabilityAssignmentTarget(project_id=project_id),
    )

    created = asyncio.run(
        service.create(
            owner_ref=OWNER,
            content=CapabilityAssignmentContent(
                target=target,
                required=(CapabilityAssignmentRule("tool.echo"),),
            ),
            access=_access(),
        )
    )

    assert created.project_id == project_id
    assert repository.get(created.assignment_id).project_id == project_id
    restarted = JsonCapabilityAssignmentRepository(path)
    assert restarted.get(created.assignment_id).project_id == project_id
    assert restarted.get_revision(created.assignment_id, 1) == created


def test_revise_preserves_exact_history_and_rejects_stale_revision() -> None:
    target = _target()
    repository = InMemoryCapabilityAssignmentRepository()
    service = _service(
        target,
        repository=repository,
        specs=(CapabilitySpec(capability_id="tool.echo", name="Echo", version="1.0"),),
    )
    first = asyncio.run(
        service.create(
            owner_ref=OWNER,
            content=CapabilityAssignmentContent(
                target=target,
                required=(CapabilityAssignmentRule("tool.echo"),),
            ),
            access=_access(),
        )
    )
    second = asyncio.run(
        service.revise(
            first.assignment_id,
            CapabilityAssignmentContent(
                target=target,
                allowed=(CapabilityAssignmentRule("tool.echo"),),
            ),
            access=_access(),
            expected_revision=1,
        )
    )

    assert second.revision == 2
    assert repository.get_revision(first.assignment_id, 1) == first
    assert repository.get_revision(first.assignment_id, 2) == second

    with pytest.raises(ContractError) as stale:
        asyncio.run(
            service.revise(
                first.assignment_id,
                CapabilityAssignmentContent(
                    target=target,
                    denied=(CapabilityAssignmentRule("tool.echo"),),
                ),
                access=_access(),
                expected_revision=1,
            )
        )
    assert stale.value.code is ErrorCode.CONFLICT
    assert repository.list_revisions(first.assignment_id) == (first, second)


def test_exact_version_and_feature_constraints_are_registry_backed() -> None:
    target = _target()
    service = _service(
        target,
        repository=InMemoryCapabilityAssignmentRepository(),
        specs=(
            CapabilitySpec(
                capability_id="tool.echo",
                name="Echo 1",
                version="1.0",
                features=("text",),
            ),
            CapabilitySpec(
                capability_id="tool.echo",
                name="Echo 2",
                version="2.0",
                features=("text", "json"),
            ),
        ),
    )

    compatible = asyncio.run(
        service.create(
            owner_ref=OWNER,
            content=CapabilityAssignmentContent(
                target=target,
                required=(
                    CapabilityAssignmentRule(
                        "tool.echo",
                        compatibility=CapabilityCompatibilityRequest(
                            minimum_version="2.0",
                            maximum_version="3.0",
                            required_features=("json",),
                        ),
                    ),
                ),
            ),
            access=_access(),
        )
    )
    assert compatible.content.required[0].compatibility is not None

    with pytest.raises(ContractError) as exact_version:
        asyncio.run(
            service.create(
                owner_ref=OWNER,
                content=CapabilityAssignmentContent(
                    target=target,
                    required=(CapabilityAssignmentRule("tool.echo", exact_version="3.0"),),
                ),
                access=_access(),
            )
        )
    assert exact_version.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
