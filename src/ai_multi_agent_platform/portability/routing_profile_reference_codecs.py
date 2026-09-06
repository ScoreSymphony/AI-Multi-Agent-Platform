"""#309-aware portability wrappers for Agent and Template routing-profile references."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents import agent_profile_from_json
from ai_multi_agent_platform.agents.models import AgentProfile
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.control_plane.models import json_value
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.templates.models import TemplateContent, TemplateType

from .agent_codecs import (
    AgentPortableCodec,
    AgentPortableSnapshot,
    AgentTeamPortableCodec,
)
from .dependencies import parse_resource_dependency, resource_dependency
from .model_routing_profile_codecs import MODEL_ROUTING_PROFILE_RESOURCE_TYPE
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry
from .template_codecs import TemplatePortableCodec, TemplatePortableSnapshot

_LEGACY_TEMPLATE_POLICY_RESOURCE_TYPE = "model_routing_policy"


class RoutingProfileAwareAgentPortableCodec(AgentPortableCodec):
    """Add exact #309 dependencies/remapping to the canonical Agent codec."""

    def serialize(self, value: object) -> ResourceExport:
        exported = super().serialize(value)
        if not isinstance(value, AgentPortableSnapshot):
            return exported
        dependencies = set(exported.dependencies)
        for revision in value.revisions:
            reference = _parse_exact_ref(revision.profile.model.routing_profile_ref)
            if reference is None:
                continue
            dependencies.add(_profile_dependency(reference, purpose="Agent model-routing profile"))
        return replace(exported, dependencies=_sorted_dependencies(dependencies))

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        value = super().deserialize(resource, context)
        if not isinstance(value, AgentPortableSnapshot):
            return value
        revisions = tuple(
            replace(
                revision,
                profile=_remap_agent_profile(revision.profile, context),
            )
            for revision in value.revisions
        )
        return AgentPortableSnapshot(value.definition, revisions)


class RoutingProfileAwareTemplatePortableCodec(TemplatePortableCodec):
    """Map exact Template model-policy refs onto the canonical #309 resource type."""

    def serialize(self, value: object) -> ResourceExport:
        exported = super().serialize(value)
        if not isinstance(value, TemplatePortableSnapshot):
            return exported
        dependencies = {
            item for item in exported.dependencies if not _is_exact_legacy_profile_dependency(item)
        }
        for revision in value.revisions:
            for raw_reference in revision.content.requirements.model_policy_refs:
                reference = _parse_exact_ref(raw_reference)
                if reference is None:
                    continue
                dependencies.add(
                    _profile_dependency(reference, purpose="Template model-routing profile")
                )
        return replace(exported, dependencies=_sorted_dependencies(dependencies))

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        value = super().deserialize(resource, context)
        if not isinstance(value, TemplatePortableSnapshot):
            return value
        revisions = tuple(
            replace(
                revision,
                content=_remap_template_content(revision.content, context),
            )
            for revision in value.revisions
        )
        return TemplatePortableSnapshot(value.definition, revisions)


def register_routing_profile_aware_agent_portability_codecs(
    registry: ResourceSerializerRegistry,
    *,
    agent_id_policy: IdPolicy = IdPolicy.PRESERVE,
    team_id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(RoutingProfileAwareAgentPortableCodec(id_policy=agent_id_policy))
    registry.register(AgentTeamPortableCodec(id_policy=team_id_policy))


def register_routing_profile_aware_template_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(RoutingProfileAwareTemplatePortableCodec(id_policy=id_policy))


def _remap_agent_profile(profile: AgentProfile, context: ImportContext) -> AgentProfile:
    routing_profile_ref = profile.model.routing_profile_ref
    if routing_profile_ref is None:
        return profile
    remapped = _remap_exact_ref(routing_profile_ref, context)
    if remapped == routing_profile_ref:
        return profile
    return replace(
        profile,
        model=replace(profile.model, routing_profile_ref=remapped),
    )


def _remap_template_content(content: TemplateContent, context: ImportContext) -> TemplateContent:
    requirements = replace(
        content.requirements,
        model_policy_refs=tuple(
            _remap_exact_ref(item, context) for item in content.requirements.model_policy_refs
        ),
    )
    configuration = content.configuration
    if content.template_type is not TemplateType.AGENT or configuration.payload is None:
        return replace(content, requirements=requirements)

    raw_profile = configuration.payload.get("profile")
    if raw_profile is None:
        return replace(content, requirements=requirements)
    try:
        profile = agent_profile_from_json(raw_profile)
    except (TypeError, ValueError):
        # Preserve malformed payloads for the canonical Template validator to reject with
        # its existing domain-specific error instead of changing portability error semantics.
        return replace(content, requirements=requirements)
    remapped_profile = _remap_agent_profile(profile, context)
    if remapped_profile == profile:
        return replace(content, requirements=requirements)
    serialized_profile = json_value(remapped_profile)
    if not isinstance(serialized_profile, dict):
        return replace(content, requirements=requirements)
    payload = dict(configuration.payload)
    payload["profile"] = _freeze_json(serialized_profile)
    return replace(
        content,
        requirements=requirements,
        configuration=replace(configuration, payload=payload),
    )


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, dict):
        return {key: _freeze_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _remap_exact_ref(value: str, context: ImportContext) -> str:
    reference = _parse_exact_ref(value)
    if reference is None:
        return value
    target_id = context.remap(MODEL_ROUTING_PROFILE_RESOURCE_TYPE, reference.profile_id)
    return ModelRoutingProfileRef(target_id, reference.revision).canonical_ref


def _parse_exact_ref(value: str | None) -> ModelRoutingProfileRef | None:
    if value is None:
        return None
    try:
        return ModelRoutingProfileRef.parse(value)
    except ValueError:
        return None


def _profile_dependency(
    reference: ModelRoutingProfileRef,
    *,
    purpose: str,
) -> DependencyRequirement:
    return resource_dependency(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        reference.profile_id,
        version_constraint=f"=={reference.revision}",
        purpose=purpose,
    )


def _is_exact_legacy_profile_dependency(item: DependencyRequirement) -> bool:
    if item.kind is not DependencyKind.RESOURCE:
        return False
    reference = parse_resource_dependency(item)
    return (
        reference.resource_type == _LEGACY_TEMPLATE_POLICY_RESOURCE_TYPE
        and _parse_exact_ref(reference.resource_id) is not None
    )


def _sorted_dependencies(
    dependencies: set[DependencyRequirement],
) -> tuple[DependencyRequirement, ...]:
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (
                item.kind.value,
                item.identifier,
                item.required,
                item.version_constraint or "",
                item.purpose or "",
            ),
        )
    )


__all__ = [
    "RoutingProfileAwareAgentPortableCodec",
    "RoutingProfileAwareTemplatePortableCodec",
    "register_routing_profile_aware_agent_portability_codecs",
    "register_routing_profile_aware_template_portability_codec",
]
