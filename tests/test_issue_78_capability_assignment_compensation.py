from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import pytest

from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentProvenance,
    CapabilityAssignmentRevision,
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
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ProposedAction, RiskClassification
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.capability_assignment_handler import (
    register_capability_assignment_template_handler,
)
from ai_multi_agent_platform.templates.models import (
    CapabilityRequirement,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateInstantiationProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateEnvironment

OWNER = OwnerRef(type="user", id="issue-78-compensation-owner")


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


class _Targets:
    def resolve(
        self,
        target: CapabilityAssignmentTarget,
    ) -> ResolvedCapabilityAssignmentTarget:
        del target
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


@dataclass(slots=True)
class _FailingWorkspaceHandler:
    template_type = TemplateType.WORKSPACE_STRUCTURE

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (
            TemplateResourceChange(
                resource_type="workspace",
                action="create",
                description="Synthetic later failure",
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance, context
        raise RuntimeError("synthetic later Template failure")


def _service(
    repository: InMemoryCapabilityAssignmentRepository,
) -> CapabilityAssignmentService:
    return CapabilityAssignmentService(
        repository=repository,
        capabilities=_Inventory(
            (
                CapabilitySpec(
                    capability_id="tool.echo",
                    name="Echo",
                    version="1.0",
                ),
            )
        ),
        targets=_Targets(),
        authorization=_AllowGate(),
    )


def _publish(
    application: TemplateApplicationService,
    content: TemplateContent,
) -> TemplateRevision:
    draft = application.templates.create_draft(owner_ref=OWNER, content=content)
    return application.templates.publish(draft.template_id, expected_revision=1)


def test_composite_failure_compensates_earlier_capability_assignment() -> None:
    assignments = InMemoryCapabilityAssignmentRepository()
    handlers = ContextualTemplateHandlerRegistry()
    register_capability_assignment_template_handler(handlers, _service(assignments))
    handlers.register(_FailingWorkspaceHandler())
    application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)
    target_id = new_id("agent")

    assignment = _publish(
        application,
        TemplateContent(
            name="Capability assignment",
            description="Created before the later failing dependency",
            template_type=TemplateType.CAPABILITY_ASSIGNMENT,
            configuration=TemplateConfiguration(
                payload={
                    "target": {"subject_type": "agent", "subject_id": target_id},
                    "required": ({"capability_id": "tool.echo"},),
                }
            ),
            requirements=TemplateRequirements(
                capabilities=(CapabilityRequirement("tool.echo"),)
            ),
        ),
    )
    failing = _publish(
        application,
        TemplateContent(
            name="Failing workspace",
            description="Synthetic rollback trigger",
            template_type=TemplateType.WORKSPACE_STRUCTURE,
            configuration=TemplateConfiguration(payload={}),
        ),
    )
    composite = _publish(
        application,
        TemplateContent(
            name="Composite rollback",
            description="Assignment must not survive a later dependency failure",
            template_type=TemplateType.COMPOSITE,
            configuration=TemplateConfiguration(payload={}),
            dependencies=(
                TemplateDependency(assignment.template_id, revision=assignment.revision),
                TemplateDependency(failing.template_id, revision=failing.revision),
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="synthetic later Template failure"):
        asyncio.run(
            application.apply(
                composite.template_id,
                applied_by=OWNER,
                revision=composite.revision,
                environment=TemplateEnvironment(
                    capability_ids=frozenset({"tool.echo"})
                ),
            )
        )

    assert assignments.list() == ()


def test_compensation_refuses_independently_revised_assignment() -> None:
    repository = InMemoryCapabilityAssignmentRepository()
    assignment_id = new_id("cap_assignment")
    target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    content = CapabilityAssignmentContent(
        target=target,
        provenance=CapabilityAssignmentProvenance(
            source="template:template_example@2",
            creator_ref=OWNER.id,
        ),
    )
    policy = CapabilityAssignmentPolicy(
        assignment_id=assignment_id,
        owner_ref=OWNER,
        current_revision=1,
    )
    revision = CapabilityAssignmentRevision(
        assignment_id=assignment_id,
        revision=1,
        owner_ref=OWNER,
        content=content,
    )
    repository.create(policy, revision)
    repository.append_revision(
        replace(policy, current_revision=2),
        replace(revision, revision=2),
    )

    with pytest.raises(ContractError) as blocked:
        repository.compensate_created(
            assignment_id,
            expected_owner_ref=OWNER,
            expected_source="template:template_example@2",
        )

    assert blocked.value.code is ErrorCode.CONFLICT
    assert repository.get(assignment_id).current_revision == 2


def test_json_repository_persists_successful_compensation(tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "capability-assignments.json"
    repository = JsonCapabilityAssignmentRepository(path)
    assignment_id = new_id("cap_assignment")
    source = "template:template_example@2"
    content = CapabilityAssignmentContent(
        target=CapabilityAssignmentTarget(
            subject_type=CapabilityAssignmentTargetType.AGENT,
            subject_id=new_id("agent"),
        ),
        provenance=CapabilityAssignmentProvenance(source=source, creator_ref=OWNER.id),
    )
    policy = CapabilityAssignmentPolicy(
        assignment_id=assignment_id,
        owner_ref=OWNER,
        current_revision=1,
    )
    repository.create(
        policy,
        CapabilityAssignmentRevision(
            assignment_id=assignment_id,
            revision=1,
            owner_ref=OWNER,
            content=content,
        ),
    )

    repository.compensate_created(
        assignment_id,
        expected_owner_ref=OWNER,
        expected_source=source,
    )

    restored = JsonCapabilityAssignmentRepository(path)
    assert restored.list() == ()
