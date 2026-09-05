"""Template integration for canonical capability-assignment policy resources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest
from ai_multi_agent_platform.capability_assignments import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentContent,
    CapabilityAssignmentProvenance,
    CapabilityAssignmentRule,
    CapabilityAssignmentService,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateInstantiationProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)


@dataclass(frozen=True, slots=True)
class _TargetSpec:
    subject_type: CapabilityAssignmentTargetType
    subject_id: str | None = None
    template_id: str | None = None
    template_revision: int | None = None


@dataclass(frozen=True, slots=True)
class _Configuration:
    target: _TargetSpec
    required: tuple[CapabilityAssignmentRule, ...]
    allowed: tuple[CapabilityAssignmentRule, ...]
    denied: tuple[CapabilityAssignmentRule, ...]

    @property
    def privileged(self) -> bool:
        return any(
            rule.privileged or rule.approval_required for rule in self.required + self.allowed
        )


@dataclass(slots=True)
class CapabilityAssignmentTemplateHandler:
    """Instantiate Templates through the #366 owning service, never Template persistence."""

    service: CapabilityAssignmentService
    template_type = TemplateType.CAPABILITY_ASSIGNMENT

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        config = _configuration(revision)
        return (
            TemplateResourceChange(
                resource_type="capability_assignment",
                action="create",
                description=(
                    "Create canonical capability assignment from "
                    f"{revision.template_id}@{revision.revision}"
                ),
                privileged=config.privileged,
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        config = _configuration(revision)
        target = _resolve_target(config.target, context)
        access = _access_context(revision, provenance, context)
        created = await self.service.create(
            owner_ref=provenance.applied_by,
            content=CapabilityAssignmentContent(
                target=target,
                required=config.required,
                allowed=config.allowed,
                denied=config.denied,
                provenance=CapabilityAssignmentProvenance(
                    source=f"template:{revision.template_id}@{revision.revision}",
                    creator_ref=access.actor.actor_id,
                ),
            ),
            access=access,
            project_id=revision.project_id,
            organization_id=revision.organization_id,
        )
        return (
            TemplateResourceRef(
                resource_type="capability_assignment",
                resource_id=created.assignment_id,
            ),
        )


def register_capability_assignment_template_handler(
    registry: ContextualTemplateHandlerRegistry,
    service: CapabilityAssignmentService,
) -> None:
    registry.register(CapabilityAssignmentTemplateHandler(service))


def _configuration(revision: TemplateRevision) -> _Configuration:
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "canonical capability-assignment handler requires an inline Template payload",
        )
    data = cast(Mapping[str, object], payload)
    _reject_unknown(data, {"target", "required", "allowed", "denied"}, "configuration")
    try:
        return _Configuration(
            target=_target_spec(_required_mapping(data, "target")),
            required=_rules(data, "required"),
            allowed=_rules(data, "allowed"),
            denied=_rules(data, "denied"),
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid capability-assignment Template configuration: {exc}",
        ) from exc


def _target_spec(data: Mapping[str, object]) -> _TargetSpec:
    _reject_unknown(
        data,
        {"subject_type", "subject_id", "template_id", "revision"},
        "target",
    )
    subject_type = CapabilityAssignmentTargetType(_required_string(data, "subject_type"))
    subject_id = _optional_string(data, "subject_id")
    template_id = _optional_string(data, "template_id")
    if (subject_id is None) == (template_id is None):
        raise ValueError("target requires exactly one of subject_id or template_id")
    template_revision = _optional_positive_int(data, "revision")
    if template_revision is not None and template_id is None:
        raise ValueError("target revision is valid only with template_id")
    if subject_id is not None:
        return _TargetSpec(subject_type=subject_type, subject_id=subject_id)
    assert template_id is not None
    validate_id(template_id, "template")
    return _TargetSpec(
        subject_type=subject_type,
        template_id=template_id,
        template_revision=template_revision,
    )


def _resolve_target(
    target: _TargetSpec,
    context: TemplateInstantiationContext,
) -> CapabilityAssignmentTarget:
    if target.subject_id is not None:
        subject_id = target.subject_id
    else:
        assert target.template_id is not None
        resource = context.single_resource_for(
            target.template_id,
            revision=target.template_revision,
            resource_type=target.subject_type.value,
        )
        subject_id = resource.resource_id
    return CapabilityAssignmentTarget(subject_type=target.subject_type, subject_id=subject_id)


def _rules(data: Mapping[str, object], name: str) -> tuple[CapabilityAssignmentRule, ...]:
    value = data.get(name, ())
    if not isinstance(value, list | tuple):
        raise ValueError(f"{name} must be an array")
    return tuple(_rule(_mapping(item, f"{name} item")) for item in value)


def _rule(data: Mapping[str, object]) -> CapabilityAssignmentRule:
    _reject_unknown(
        data,
        {
            "capability_id",
            "exact_version",
            "compatibility",
            "privileged",
            "approval_required",
        },
        "capability rule",
    )
    raw_compatibility = data.get("compatibility")
    compatibility = None
    if raw_compatibility is not None:
        compatibility = _compatibility(_mapping(raw_compatibility, "compatibility"))
    return CapabilityAssignmentRule(
        capability_id=_required_string(data, "capability_id"),
        exact_version=_optional_string(data, "exact_version"),
        compatibility=compatibility,
        privileged=_optional_bool(data, "privileged", default=False),
        approval_required=_optional_bool(data, "approval_required", default=False),
    )


def _compatibility(data: Mapping[str, object]) -> CapabilityCompatibilityRequest:
    _reject_unknown(
        data,
        {
            "minimum_version",
            "maximum_version",
            "include_minimum",
            "include_maximum",
            "required_features",
        },
        "compatibility",
    )
    features = data.get("required_features", ())
    if not isinstance(features, list | tuple):
        raise ValueError("compatibility.required_features must be an array")
    required_features = tuple(_string(item, "required feature") for item in features)
    return CapabilityCompatibilityRequest(
        minimum_version=_optional_string(data, "minimum_version"),
        maximum_version=_optional_string(data, "maximum_version"),
        include_minimum=_optional_bool(data, "include_minimum", default=True),
        include_maximum=_optional_bool(data, "include_maximum", default=False),
        required_features=required_features,
    )


def _access_context(
    revision: TemplateRevision,
    provenance: TemplateInstantiationProvenance,
    context: TemplateInstantiationContext,
) -> CapabilityAssignmentAccessContext:
    actor_type = {
        "user": ActorType.HUMAN,
        "service": ActorType.SERVICE,
    }.get(provenance.applied_by.type)
    if actor_type is None:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            (
                "capability-assignment Template application requires an attributable "
                "user or service actor"
            ),
        )
    return CapabilityAssignmentAccessContext(
        actor=ActorIdentity(
            actor_id=provenance.applied_by.id,
            actor_type=actor_type,
        ),
        operation=OperationContext(
            correlation_id=f"template-instance:{context.instance_id}",
            owner_type=provenance.applied_by.type,
            owner_id=provenance.applied_by.id,
            project_id=revision.project_id,
        ),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _required_mapping(data: Mapping[str, object], name: str) -> Mapping[str, object]:
    if name not in data:
        raise ValueError(f"missing required field: {name}")
    return _mapping(data[name], name)


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _required_string(data: Mapping[str, object], name: str) -> str:
    if name not in data:
        raise ValueError(f"missing required field: {name}")
    return _string(data[name], name)


def _optional_string(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    return _string(value, name)


def _optional_positive_int(data: Mapping[str, object], name: str) -> int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_bool(data: Mapping[str, object], name: str, *, default: bool) -> bool:
    value = data.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _reject_unknown(data: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(unknown)}")
