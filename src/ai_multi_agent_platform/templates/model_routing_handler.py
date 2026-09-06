"""Template integration for canonical durable model-routing profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models import (
    ModelRoutingProfilePolicy,
    ModelRoutingProfileService,
    RoutingProfileFallbackPolicy,
    RoutingRequirements,
)

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateInstantiationProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)


@dataclass(slots=True)
class ModelRoutingPolicyTemplateHandler:
    """Instantiate ``model_routing_policy`` Templates through the canonical #309 service."""

    service: ModelRoutingProfileService
    template_type = TemplateType.MODEL_ROUTING_POLICY

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        _policy(revision)
        return (
            TemplateResourceChange(
                resource_type="model_routing_profile",
                action="create",
                description=(
                    f"Create Model Routing Profile from "
                    f"{revision.template_id}@{revision.revision}"
                ),
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        created = await self.service.create_profile(
            name=revision.content.name,
            description=revision.content.description,
            policy=_policy(revision),
            owner_ref=provenance.applied_by,
            principal_ref=_actor_ref(provenance.applied_by),
            context=OperationContext(
                correlation_id=f"template-instance:{context.instance_id}",
                owner_type=provenance.applied_by.type,
                owner_id=provenance.applied_by.id,
                project_id=revision.project_id,
            ),
            project_id=revision.project_id,
            provenance=Provenance(
                source=f"template:{provenance.source.template_id}@{provenance.source.revision}",
                actor_ref=_actor_ref(provenance.applied_by),
                details={"template_instance_id": context.instance_id},
            ),
        )
        return (
            TemplateResourceRef(
                resource_type="model_routing_profile",
                resource_id=created.profile_id,
            ),
        )

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        expected_source = f"template:{provenance.source.template_id}@{provenance.source.revision}"
        for resource in reversed(resources):
            if resource.resource_type != "model_routing_profile":
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Model Routing Policy Template compensation received an unexpected resource",
                    details={
                        "resource_type": resource.resource_type,
                        "resource_id": resource.resource_id,
                    },
                )
            self.service.compensate_created(
                resource.resource_id,
                expected_owner_ref=provenance.applied_by,
                expected_source=expected_source,
                expected_instance_id=context.instance_id,
            )


def register_model_routing_policy_template_handler(
    registry: ContextualTemplateHandlerRegistry,
    service: ModelRoutingProfileService,
) -> None:
    registry.register(ModelRoutingPolicyTemplateHandler(service))


def _policy(revision: TemplateRevision) -> ModelRoutingProfilePolicy:
    if revision.organization_id is not None:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Model Routing Policy Template cannot preserve independent organization scope "
            "in canonical #309 routing profiles",
            details={"organization_id": revision.organization_id},
        )
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "canonical Model Routing Policy handler requires an inline Template payload",
        )
    try:
        root = _mapping(payload, "Model Routing Policy Template configuration")
        _reject_unknown(root, {"policy"}, "configuration")
        policy = _mapping(root.get("policy"), "policy")
        _reject_unknown(policy, {"requirements", "preferred_model_ids", "fallback"}, "policy")
        requirements = _mapping(policy.get("requirements", {}), "requirements")
        _reject_unknown(
            requirements,
            {
                "explicit_model_id",
                "min_context_window",
                "tool_calling",
                "structured_output",
                "streaming",
                "modalities",
                "reasoning",
                "local_only",
                "self_hosted_only",
            },
            "requirements",
        )
        return ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(
                explicit_model_id=_optional_string(requirements, "explicit_model_id"),
                min_context_window=_optional_positive_int(requirements, "min_context_window"),
                tool_calling=_optional_bool(requirements, "tool_calling", False),
                structured_output=_optional_bool(requirements, "structured_output", False),
                streaming=_optional_bool(requirements, "streaming", False),
                modalities=_string_tuple(requirements, "modalities"),
                reasoning=_string_tuple(requirements, "reasoning"),
                local_only=_optional_bool(requirements, "local_only", False),
                self_hosted_only=_optional_bool(requirements, "self_hosted_only", False),
            ),
            preferred_model_ids=_string_tuple(policy, "preferred_model_ids"),
            fallback=RoutingProfileFallbackPolicy(
                _optional_string(policy, "fallback") or RoutingProfileFallbackPolicy.ROUTE.value
            ),
        )
    except ContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid Model Routing Policy Template configuration: {exc}",
        ) from exc


def _actor_ref(owner: OwnerRef) -> str:
    if owner.id.startswith(f"{owner.type}_") or owner.id.startswith(f"{owner.type}:"):
        return owner.id
    return f"{owner.type}:{owner.id}"


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _optional_string(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_positive_int(data: Mapping[str, object], name: str) -> int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_bool(data: Mapping[str, object], name: str, default: bool) -> bool:
    value = data.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _string_tuple(data: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = data.get(name, ())
    if not isinstance(value, list | tuple):
        raise ValueError(f"{name} must be an array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} must contain only non-blank strings")
    return tuple(cast(str, item) for item in value)


def _reject_unknown(data: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(unknown)}")
