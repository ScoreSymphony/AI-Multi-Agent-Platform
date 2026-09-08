"""#79 portable codec for canonical #589 Research Evidence bundles."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.research import (
    RESEARCH_BUNDLE_KIND,
    RESEARCH_BUNDLE_SCHEMA_VERSION,
    ResearchItem,
    ResearchService,
    export_research_bundle,
)
from ai_multi_agent_platform.research.repository import _decode

from .dependencies import resource_dependency
from .models import DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

RESEARCH_BUNDLE_RESOURCE_TYPE = "research_item_bundle"
RESEARCH_BUNDLE_PORTABLE_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class PortableResearchBundle:
    """Verified portable payload awaiting explicit Research-domain import."""

    research_item_id: str
    bundle: dict[str, JsonValue]


def portable_research_bundle(
    research: ResearchService,
    research_item_id: str,
) -> PortableResearchBundle:
    return PortableResearchBundle(
        research_item_id=research_item_id,
        bundle=export_research_bundle(research, research_item_id),
    )


class ResearchBundlePortableCodec:
    """Carry exact Research history through #79 without activating or trusting it."""

    resource_type = RESEARCH_BUNDLE_RESOURCE_TYPE

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, PortableResearchBundle):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Research portability codec requires PortableResearchBundle",
            )
        item = _bundle_item(value.bundle)
        if item.research_item_id != value.research_item_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Research bundle identity disagrees with canonical ResearchItem",
            )
        return ResourceExport(
            resource_id=item.research_item_id,
            resource_version=RESEARCH_BUNDLE_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": RESEARCH_BUNDLE_PORTABLE_SCHEMA_VERSION,
                "bundle": value.bundle,
            },
            id_policy=IdPolicy.HISTORICAL_PRESERVE,
            dependencies=_dependencies(value.bundle, item),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        del context
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Research bundle codec received another resource type",
            )
        if resource.id_policy is not IdPolicy.HISTORICAL_PRESERVE:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "Research evidence portability requires historical identity preservation",
            )
        if resource.resource_version != RESEARCH_BUNDLE_PORTABLE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "unsupported portable Research bundle schema version",
            )
        if resource.payload.get("schema_version") != RESEARCH_BUNDLE_PORTABLE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "unsupported Research portability payload schema version",
            )
        raw_bundle = resource.payload.get("bundle")
        if not isinstance(raw_bundle, dict):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable Research payload must contain a bundle object",
            )
        bundle = raw_bundle
        item = _bundle_item(bundle)
        if item.research_item_id != resource.resource_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Research resource ID disagrees with bundle identity",
            )
        return PortableResearchBundle(research_item_id=item.research_item_id, bundle=bundle)


def register_research_bundle_portability_codec(registry: ResourceSerializerRegistry) -> None:
    registry.register(ResearchBundlePortableCodec())


def _bundle_item(bundle: dict[str, JsonValue]) -> ResearchItem:
    if bundle.get("schema_version") != RESEARCH_BUNDLE_SCHEMA_VERSION:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "invalid Research bundle schema")
    if bundle.get("kind") != RESEARCH_BUNDLE_KIND:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "invalid Research bundle kind")
    try:
        item = _decode(bundle.get("research_item"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "invalid Research bundle item"
        ) from exc
    if not isinstance(item, ResearchItem):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "invalid Research bundle item")
    return item


def _dependencies(
    bundle: dict[str, JsonValue],
    item: ResearchItem,
) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    for resource_type, resource_id, purpose in (
        ("project", item.project_id, "Research project provenance"),
        ("workspace", item.workspace_id, "Research workspace provenance"),
        ("task", item.task_id, "Research Task provenance"),
        ("plan", item.plan_id, "Research Plan provenance"),
        ("run", item.run_id, "Research Run provenance"),
    ):
        if resource_id is not None:
            dependencies.add(
                resource_dependency(
                    resource_type,
                    resource_id,
                    required=False,
                    purpose=purpose,
                )
            )

    bindings = bundle.get("verification_bindings")
    if isinstance(bindings, list):
        for raw in bindings:
            try:
                decoded = _decode(raw)
            except (KeyError, TypeError, ValueError):
                continue
            verification_id = getattr(decoded, "verification_id", None)
            if isinstance(verification_id, str):
                dependencies.add(
                    resource_dependency(
                        "verification",
                        verification_id,
                        required=True,
                        purpose="Research Verification authority",
                    )
                )
    return tuple(sorted(dependencies, key=lambda value: value.identifier))


__all__ = [
    "RESEARCH_BUNDLE_PORTABLE_SCHEMA_VERSION",
    "RESEARCH_BUNDLE_RESOURCE_TYPE",
    "PortableResearchBundle",
    "ResearchBundlePortableCodec",
    "portable_research_bundle",
    "register_research_bundle_portability_codec",
]
