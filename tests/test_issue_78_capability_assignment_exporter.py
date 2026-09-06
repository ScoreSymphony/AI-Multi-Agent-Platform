from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest, CapabilitySpec
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentContent,
    CapabilityAssignmentProvenance,
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
    OperationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, ProposedAction, RiskClassification
from ai_multi_agent_platform.templates import (
    CapabilityAssignmentTemplateExporter,
    InMemoryTemplateRepository,
    TemplateService,
    TemplateType,
)

OWNER = OwnerRef(type="user", id="issue-78-capability-export-owner")
ACCESS = CapabilityAssignmentAccessContext(
    actor=ActorIdentity(OWNER.id, ActorType.HUMAN),
    operation=OperationContext(
        correlation_id="issue-78-capability-export",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
    ),
)


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

    def resolve(
        self,
        target: CapabilityAssignmentTarget,
    ) -> ResolvedCapabilityAssignmentTarget:
        if target != self.target:
            raise AssertionError(f"unexpected target: {target}")
        return ResolvedCapabilityAssignmentTarget()


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


def _service(
    target: CapabilityAssignmentTarget,
    *capabilities: CapabilitySpec,
) -> CapabilityAssignmentService:
    return CapabilityAssignmentService(
        repository=InMemoryCapabilityAssignmentRepository(),
        capabilities=_Inventory(tuple(capabilities)),
        targets=_Targets(target),
        authorization=_AllowGate(),
    )


def test_export_preserves_exact_revision_rules_requirements_and_provenance() -> None:
    async def scenario() -> None:
        target = CapabilityAssignmentTarget(
            CapabilityAssignmentTargetType.AGENT,
            new_id("agent"),
        )
        service = _service(
            target,
            CapabilitySpec(capability_id="tool.echo", name="Echo", version="2.1"),
            CapabilitySpec(capability_id="tool.read", name="Read", version="1.5"),
        )
        created = await service.create(
            owner_ref=OWNER,
            access=ACCESS,
            content=CapabilityAssignmentContent(
                target=target,
                required=(
                    CapabilityAssignmentRule(
                        capability_id="tool.echo",
                        exact_version="2.1",
                        privileged=True,
                        approval_required=True,
                    ),
                ),
                allowed=(
                    CapabilityAssignmentRule(
                        capability_id="tool.read",
                        compatibility=CapabilityCompatibilityRequest(
                            minimum_version="1.0",
                            maximum_version="2.0",
                            include_minimum=True,
                            include_maximum=False,
                        ),
                    ),
                ),
                provenance=CapabilityAssignmentProvenance(
                    source="canonical-test-source",
                    creator_ref=OWNER.id,
                ),
            ),
        )
        revised = await service.revise(
            created.assignment_id,
            CapabilityAssignmentContent(
                target=target,
                required=(CapabilityAssignmentRule("tool.echo", exact_version="2.1"),),
                denied=(CapabilityAssignmentRule("tool.read"),),
                provenance=CapabilityAssignmentProvenance(
                    source="canonical-test-revision",
                    creator_ref=OWNER.id,
                ),
            ),
            access=ACCESS,
            expected_revision=1,
        )
        templates = TemplateService(InMemoryTemplateRepository())
        exporter = CapabilityAssignmentTemplateExporter(service, templates)

        exported = await exporter.create_from_assignment(
            created.assignment_id,
            access=ACCESS,
            owner_ref=OWNER,
            author="user:issue-78-capability-export-owner",
            revision=created.revision,
            name="Reusable capability policy",
        )

        assert revised.revision == 2
        assert exported.content.template_type is TemplateType.CAPABILITY_ASSIGNMENT
        assert exported.content.name == "Reusable capability policy"
        payload = exported.content.configuration.payload
        assert payload is not None
        assert payload["target"] == {
            "subject_type": "agent",
            "subject_id": target.subject_id,
        }
        required = payload["required"]
        allowed = payload["allowed"]
        assert isinstance(required, tuple)
        assert isinstance(allowed, tuple)
        assert required[0]["exact_version"] == "2.1"
        assert required[0]["privileged"] is True
        assert required[0]["approval_required"] is True
        assert allowed[0]["compatibility"]["minimum_version"] == "1.0"
        assert allowed[0]["compatibility"]["maximum_version"] == "2.0"

        requirements = {
            item.capability_id: item for item in exported.content.requirements.capabilities
        }
        assert requirements["tool.echo"].version_constraint == "==2.1"
        assert requirements["tool.echo"].privileged is True
        assert requirements["tool.read"].version_constraint == ">=1.0,<2.0"
        assert exported.content.provenance.source == "canonical-capability-assignment-export"
        assert exported.content.provenance.metadata["source_resource_revision"] == 1
        assert exported.content.provenance.metadata["source_target_id"] == target.subject_id

    asyncio.run(scenario())


def test_export_without_revision_uses_current_canonical_revision() -> None:
    async def scenario() -> None:
        target = CapabilityAssignmentTarget(
            CapabilityAssignmentTargetType.PROJECT,
            new_id("project"),
        )
        service = _service(
            target,
            CapabilitySpec(capability_id="tool.echo", name="Echo", version="1.0"),
        )
        created = await service.create(
            owner_ref=OWNER,
            access=ACCESS,
            content=CapabilityAssignmentContent(
                target=target,
                required=(CapabilityAssignmentRule("tool.echo"),),
            ),
        )
        await service.revise(
            created.assignment_id,
            CapabilityAssignmentContent(
                target=target,
                denied=(CapabilityAssignmentRule("tool.echo"),),
            ),
            access=ACCESS,
            expected_revision=1,
        )
        templates = TemplateService(InMemoryTemplateRepository())
        exported = await CapabilityAssignmentTemplateExporter(
            service,
            templates,
        ).create_from_assignment(
            created.assignment_id,
            access=ACCESS,
            owner_ref=OWNER,
            author=OWNER.id,
        )

        assert exported.content.provenance.metadata["source_resource_revision"] == 2
        payload = exported.content.configuration.payload
        assert payload is not None
        assert payload["required"] == ()
        denied = payload["denied"]
        assert isinstance(denied, tuple)
        assert denied[0]["capability_id"] == "tool.echo"

    asyncio.run(scenario())


def test_export_fails_closed_when_feature_requirements_cannot_be_represented() -> None:
    async def scenario() -> None:
        target = CapabilityAssignmentTarget(
            CapabilityAssignmentTargetType.AGENT,
            new_id("agent"),
        )
        service = _service(
            target,
            CapabilitySpec(
                capability_id="tool.search",
                name="Search",
                version="1.0",
                features=("citations",),
            ),
        )
        created = await service.create(
            owner_ref=OWNER,
            access=ACCESS,
            content=CapabilityAssignmentContent(
                target=target,
                required=(
                    CapabilityAssignmentRule(
                        capability_id="tool.search",
                        compatibility=CapabilityCompatibilityRequest(
                            required_features=("citations",),
                        ),
                    ),
                ),
            ),
        )
        exporter = CapabilityAssignmentTemplateExporter(
            service,
            TemplateService(InMemoryTemplateRepository()),
        )

        with pytest.raises(ContractError) as captured:
            await exporter.create_from_assignment(
                created.assignment_id,
                access=ACCESS,
                owner_ref=OWNER,
                author=OWNER.id,
            )
        assert captured.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert service.repository.get(created.assignment_id).current_revision == 1

    asyncio.run(scenario())
