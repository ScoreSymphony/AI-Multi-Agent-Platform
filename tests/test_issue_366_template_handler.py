from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentService,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
    InMemoryCapabilityAssignmentRepository,
    ResolvedCapabilityAssignmentTarget,
)
from ai_multi_agent_platform.contracts import AuthorizationDecision, AuthorizationOutcome
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ProposedAction, RiskClassification
from ai_multi_agent_platform.templates import (
    CapabilityAssignmentTemplateHandler,
    CapabilityRequirement,
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateEnvironment,
    TemplateRequirements,
    TemplateType,
    register_capability_assignment_template_handler,
)

OWNER = OwnerRef(type="user", id="issue-366-template-owner")


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


def _application(
    target: CapabilityAssignmentTarget,
) -> tuple[
    TemplateApplicationService,
    InMemoryCapabilityAssignmentRepository,
    InMemoryTemplateRepository,
]:
    assignment_repository = InMemoryCapabilityAssignmentRepository()
    assignment_service = CapabilityAssignmentService(
        repository=assignment_repository,
        capabilities=_Inventory(
            (
                CapabilitySpec(
                    capability_id="tool.echo",
                    name="Echo",
                    version="1.0",
                ),
            )
        ),
        targets=_Targets(target),
        authorization=_AllowGate(),
    )
    template_repository = InMemoryTemplateRepository()
    handlers = ContextualTemplateHandlerRegistry()
    register_capability_assignment_template_handler(handlers, assignment_service)
    return (
        TemplateApplicationService(template_repository, handlers),
        assignment_repository,
        template_repository,
    )


def test_capability_assignment_template_uses_canonical_service_without_shadow_store() -> None:
    target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    application, assignments, templates = _application(target)
    draft = application.templates.create_draft(
        owner_ref=OWNER,
        content=TemplateContent(
            name="Echo capability policy",
            description="Reusable assignment through the canonical #366 service",
            template_type=TemplateType.CAPABILITY_ASSIGNMENT,
            configuration=TemplateConfiguration(
                payload={
                    "target": {
                        "subject_type": "agent",
                        "subject_id": target.subject_id,
                    },
                    "required": ({"capability_id": "tool.echo"},),
                }
            ),
            requirements=TemplateRequirements(
                capabilities=(CapabilityRequirement("tool.echo"),)
            ),
        ),
    )
    published = application.templates.publish(draft.template_id, expected_revision=1)

    preview = application.preview(
        published.template_id,
        applied_by=OWNER,
        environment=TemplateEnvironment(capability_ids=frozenset({"tool.echo"})),
    )
    assert preview.applicable is True
    assert preview.missing_handler_types == ()
    assert preview.resource_changes[0].resource_type == "capability_assignment"

    instance = asyncio.run(
        application.apply(
            published.template_id,
            applied_by=OWNER,
            environment=TemplateEnvironment(capability_ids=frozenset({"tool.echo"})),
        )
    )

    assert len(instance.resource_refs) == 1
    resource = instance.resource_refs[0]
    assert resource.resource_type == "capability_assignment"
    policy = assignments.get(resource.resource_id)
    revision = assignments.get_revision(resource.resource_id, policy.current_revision)
    assert revision.content.target == target
    assert revision.content.required[0].capability_id == "tool.echo"
    assert revision.content.provenance.source == f"template:{published.template_id}@2"
    assert tuple(item.template_id for item in templates.list_templates()) == (
        published.template_id,
    )


def test_handler_marks_privileged_assignment_preview_explicitly() -> None:
    target = CapabilityAssignmentTarget(
        subject_type=CapabilityAssignmentTargetType.AGENT,
        subject_id=new_id("agent"),
    )
    application, _, _ = _application(target)
    draft = application.templates.create_draft(
        owner_ref=OWNER,
        content=TemplateContent(
            name="Privileged assignment",
            description="",
            template_type=TemplateType.CAPABILITY_ASSIGNMENT,
            configuration=TemplateConfiguration(
                payload={
                    "target": {
                        "subject_type": "agent",
                        "subject_id": target.subject_id,
                    },
                    "allowed": (
                        {
                            "capability_id": "tool.echo",
                            "privileged": True,
                            "approval_required": True,
                        },
                    ),
                }
            ),
        ),
    )

    handler = application.handlers.get(TemplateType.CAPABILITY_ASSIGNMENT)
    assert isinstance(handler, CapabilityAssignmentTemplateHandler)
    change = handler.preview(draft)[0]
    assert change.privileged is True
