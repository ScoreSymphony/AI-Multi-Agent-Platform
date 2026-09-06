"""Create reusable Templates from canonical capability-assignment revisions."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentRevision,
    CapabilityAssignmentRule,
    CapabilityAssignmentService,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    CapabilityRequirement,
    TemplateConfiguration,
    TemplateContent,
    TemplateProvenance,
    TemplateRequirements,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService


@dataclass(slots=True)
class CapabilityAssignmentTemplateExporter:
    """Export one authorized #366 revision without copying runtime-private state."""

    assignments: CapabilityAssignmentService
    templates: TemplateService

    async def create_from_assignment(
        self,
        assignment_id: str,
        *,
        access: CapabilityAssignmentAccessContext,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        if revision is None:
            policy = await self.assignments.get(assignment_id, access=access)
            revision = policy.current_revision
        source = await self.assignments.get_revision(
            assignment_id,
            revision,
            access=access,
        )
        _require_lossless_template_requirements(source)

        content = TemplateContent(
            name=name or _default_name(source),
            description=(
                "Template exported from canonical capability assignment "
                f"{source.assignment_id}@{source.revision}"
            ),
            template_type=TemplateType.CAPABILITY_ASSIGNMENT,
            configuration=TemplateConfiguration(payload=_configuration_payload(source)),
            requirements=TemplateRequirements(
                capabilities=tuple(
                    _capability_requirement(rule) for rule in source.content.all_rules
                )
            ),
            provenance=TemplateProvenance(
                author=author,
                source="canonical-capability-assignment-export",
                trust=TemplateTrust.LOCAL,
                metadata={
                    "source_resource_type": "capability_assignment",
                    "source_resource_id": source.assignment_id,
                    "source_resource_revision": source.revision,
                    "source_project_id": source.project_id,
                    "source_organization_id": source.organization_id,
                    "source_target_type": source.content.target.subject_type.value,
                    "source_target_id": source.content.target.subject_id,
                },
            ),
            tags=("capability-assignment", "exported"),
        )
        return self.templates.create_draft(owner_ref=owner_ref, content=content)


def _default_name(source: CapabilityAssignmentRevision) -> str:
    target = source.content.target
    return f"Capability policy for {target.subject_type.value} {target.subject_id}"


def _configuration_payload(
    source: CapabilityAssignmentRevision,
) -> dict[str, FrozenJsonValue]:
    return {
        "target": {
            "subject_type": source.content.target.subject_type.value,
            "subject_id": source.content.target.subject_id,
        },
        "required": tuple(_rule_payload(rule) for rule in source.content.required),
        "allowed": tuple(_rule_payload(rule) for rule in source.content.allowed),
        "denied": tuple(_rule_payload(rule) for rule in source.content.denied),
    }


def _rule_payload(rule: CapabilityAssignmentRule) -> dict[str, FrozenJsonValue]:
    payload: dict[str, FrozenJsonValue] = {
        "capability_id": rule.capability_id,
        "privileged": rule.privileged,
        "approval_required": rule.approval_required,
    }
    if rule.exact_version is not None:
        payload["exact_version"] = rule.exact_version
    if rule.compatibility is not None:
        compatibility = rule.compatibility
        payload["compatibility"] = {
            "minimum_version": compatibility.minimum_version,
            "maximum_version": compatibility.maximum_version,
            "include_minimum": compatibility.include_minimum,
            "include_maximum": compatibility.include_maximum,
            "required_features": tuple(compatibility.required_features),
        }
    return payload


def _capability_requirement(rule: CapabilityAssignmentRule) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=rule.capability_id,
        version_constraint=_version_constraint(rule),
        privileged=rule.privileged or rule.approval_required,
    )


def _version_constraint(rule: CapabilityAssignmentRule) -> str | None:
    if rule.exact_version is not None:
        return f"=={rule.exact_version}"
    compatibility = rule.compatibility
    if compatibility is None:
        return None
    bounds: list[str] = []
    if compatibility.minimum_version is not None:
        operator = ">=" if compatibility.include_minimum else ">"
        bounds.append(f"{operator}{compatibility.minimum_version}")
    if compatibility.maximum_version is not None:
        operator = "<=" if compatibility.include_maximum else "<"
        bounds.append(f"{operator}{compatibility.maximum_version}")
    return ",".join(bounds) or None


def _require_lossless_template_requirements(source: CapabilityAssignmentRevision) -> None:
    unsupported: list[JsonValue] = [
        rule.capability_id
        for rule in source.content.all_rules
        if _required_features(rule.compatibility)
    ]
    if unsupported:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Capability Assignment Template export cannot preserve capability feature "
            "requirements in the current Template requirement model",
            details={"capability_ids": unsupported},
        )


def _required_features(
    compatibility: CapabilityCompatibilityRequest | None,
) -> tuple[str, ...]:
    return () if compatibility is None else compatibility.required_features
