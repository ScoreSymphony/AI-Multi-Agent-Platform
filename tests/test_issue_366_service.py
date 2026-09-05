from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    CapabilityToolProvider,
    CredentialRequirement,
    ECHO_CAPABILITY_ID,
    NativeEchoProvider,
    SafetyClassification,
)
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentContent,
    CapabilityAssignmentRule,
    CapabilityAssignmentService,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
    InMemoryCapabilityAssignmentRepository,
    ResolvedCapabilityAssignmentTarget,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    ProposedAction,
    RiskClassification,
)

OWNER = OwnerRef(type="user", id="issue-366-owner")


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
    values: dict[
        tuple[CapabilityAssignmentTargetType, str],
        ResolvedCapabilityAssignmentTarget,
    ]

    def resolve(
        self,
        target: CapabilityAssignmentTarget,
    ) -> ResolvedCapabilityAssignmentTarget:
        try:
            return self.values[(target.subject_type, target.subject_id)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "canonical assignment target not found",
            ) from exc


class _Gate:
    def __init__(self, *, denied_actor: str | None = None) -> None:
        self.denied_actor = denied_actor
        self.actions: list[tuple[str, RiskClassification]] = []

    async def decide(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision:
        del approval_id
        actor_id = action.context.actor.actor_id
        self.actions.append((actor_id, risk))
        if actor_id == self.denied_actor:
            return AuthorizationDecision(AuthorizationOutcome.DENY, reason="scope denied")
        return AuthorizationDecision(AuthorizationOutcome.ALLOW)

    async def enforce(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision:
        decision = await self.decide(action, approval_id=approval_id, risk=risk)
        if decision.outcome is not AuthorizationOutcome.ALLOW:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "authorization denied",
            )
        return decision


class _ReplacementEchoProvider(CapabilityToolProvider):
    descriptor = ProviderDescriptor(
        provider_id="replacement.echo",
        provider_type="test",
        health=HealthStatus.HEALTHY,
        available=True,
    )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id=ECHO_CAPABILITY_ID,
                    name="Replacement Echo",
                    version="1.0",
                    health=HealthStatus.HEALTHY,
                    available=True,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="replacement.echo.tool",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        raise AssertionError(f"test provider must not be invoked: {invocation.invocation_id}")


def _access(
    actor_id: str = "user:allowed",
    *,
    project_id: str | None = None,
) -> CapabilityAssignmentAccessContext:
    return CapabilityAssignmentAccessContext(
        actor=ActorIdentity(actor_id=actor_id, actor_type=ActorType.HUMAN),
        operation=OperationContext(correlation_id="issue-366", project_id=project_id),
    )


def _target() -> CapabilityAssignmentTarget:
    return CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )


def _service(
    target: CapabilityAssignmentTarget,
    *,
    specs: tuple[CapabilitySpec, ...] | None = None,
    gate: _Gate | None = None,
    target_scope: ResolvedCapabilityAssignmentTarget | None = None,
) -> CapabilityAssignmentService:
    return CapabilityAssignmentService(
        repository=InMemoryCapabilityAssignmentRepository(),
        capabilities=_Inventory(
            specs
            or (
                CapabilitySpec(
                    capability_id="tool.echo",
                    name="Echo",
                    version="1.0",
                    features=("text",),
                ),
            )
        ),
        targets=_Targets(
            {
                (target.subject_type, target.subject_id): target_scope
                or ResolvedCapabilityAssignmentTarget()
            }
        ),
        authorization=gate or _Gate(),
    )


def test_invalid_capability_and_target_references_fail_before_persistence() -> None:
    target = _target()
    service = _service(target)
    missing_target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )

    with pytest.raises(ContractError) as target_error:
        asyncio.run(
            service.create(
                owner_ref=OWNER,
                content=CapabilityAssignmentContent(target=missing_target),
                access=_access(),
            )
        )
    assert target_error.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ContractError) as capability_error:
        asyncio.run(
            service.create(
                owner_ref=OWNER,
                content=CapabilityAssignmentContent(
                    target=target,
                    required=(CapabilityAssignmentRule("tool.missing"),),
                ),
                access=_access(),
            )
        )
    assert capability_error.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert service.repository.list() == ()


def test_scope_authorization_and_target_scope_are_enforced() -> None:
    target = _target()
    project_id = new_id("project")
    other_project = new_id("project")
    gate = _Gate(denied_actor="user:denied")
    service = _service(
        target,
        gate=gate,
        target_scope=ResolvedCapabilityAssignmentTarget(project_id=project_id),
    )
    created = asyncio.run(
        service.create(
            owner_ref=OWNER,
            content=CapabilityAssignmentContent(target=target),
            access=_access(project_id=project_id),
            project_id=project_id,
        )
    )

    with pytest.raises(ContractError) as authorization_error:
        asyncio.run(service.get(created.assignment_id, access=_access("user:denied")))
    assert authorization_error.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as scope_error:
        asyncio.run(
            _service(
                target,
                target_scope=ResolvedCapabilityAssignmentTarget(project_id=project_id),
            ).create(
                owner_ref=OWNER,
                content=CapabilityAssignmentContent(target=target),
                access=_access(project_id=other_project),
                project_id=other_project,
            )
        )
    assert scope_error.value.code is ErrorCode.INVALID_CONFIGURATION


def test_privileged_and_approval_metadata_are_fail_closed() -> None:
    target = _target()
    spec = CapabilitySpec(
        capability_id="tool.sensitive",
        name="Sensitive tool",
        version="1.0",
        safety=SafetyClassification.SENSITIVE,
        credential_requirement=CredentialRequirement.REQUIRED,
        required_approvals=("human",),
    )
    gate = _Gate()
    service = _service(target, specs=(spec,), gate=gate)

    with pytest.raises(ContractError) as missing_privilege:
        asyncio.run(
            service.create(
                owner_ref=OWNER,
                content=CapabilityAssignmentContent(
                    target=target,
                    allowed=(CapabilityAssignmentRule("tool.sensitive"),),
                ),
                access=_access(),
            )
        )
    assert missing_privilege.value.code is ErrorCode.INVALID_CONFIGURATION

    created = asyncio.run(
        service.create(
            owner_ref=OWNER,
            content=CapabilityAssignmentContent(
                target=target,
                allowed=(
                    CapabilityAssignmentRule(
                        "tool.sensitive",
                        privileged=True,
                        approval_required=True,
                    ),
                ),
            ),
            access=_access(),
        )
    )
    assert created.content.allowed[0].privileged is True
    assert created.content.allowed[0].approval_required is True
    assert gate.actions[-1][1] is RiskClassification.HIGH


def test_provider_replacement_preserves_canonical_assignment_identity() -> None:
    registry = CapabilityRegistry()
    asyncio.run(registry.register_provider(NativeEchoProvider()))
    target = _target()
    repository = InMemoryCapabilityAssignmentRepository()
    service = CapabilityAssignmentService(
        repository=repository,
        capabilities=registry,
        targets=_Targets(
            {(target.subject_type, target.subject_id): ResolvedCapabilityAssignmentTarget()}
        ),
        authorization=_Gate(),
    )
    created = asyncio.run(
        service.create(
            owner_ref=OWNER,
            content=CapabilityAssignmentContent(
                target=target,
                required=(CapabilityAssignmentRule(ECHO_CAPABILITY_ID),),
            ),
            access=_access(),
        )
    )

    registry.unregister_provider("native.reference")
    asyncio.run(registry.register_provider(_ReplacementEchoProvider()))

    historical = repository.get_revision(created.assignment_id, 1)
    assert historical.assignment_id == created.assignment_id
    assert historical.content.required[0].capability_id == ECHO_CAPABILITY_ID
    assert registry.inventory_capabilities()[0].capability_id == ECHO_CAPABILITY_ID
