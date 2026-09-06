"""#309-aware portability wrappers for Agent and Template routing-profile references."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents.models import AgentProfile
from ai_multi_agent_platform.models import ModelRoutingProfileRef

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
                content=replace(
                    revision.content,
                    requirements=replace(
                        revision.content.requirements,
                        model_policy_refs=tuple(
                            _remap_exact_ref(item, context)
                            for item in revision.content.requirements.model_policy_refs
                        ),
                    ),
                ),
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
