"""Resource serializer/deserializer registry for portable packages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import DependencyRequirement, IdPolicy, PortableResource
from .package import seal_resource, verify_resource


@dataclass(frozen=True, slots=True)
class ImportContext:
    """Deterministic canonical ID remapping selected by an import preview."""

    id_mapping: Mapping[tuple[str, str], str] = field(default_factory=dict)

    def remap(self, resource_type: str, resource_id: str) -> str:
        return self.id_mapping.get((resource_type, resource_id), resource_id)


@dataclass(frozen=True, slots=True)
class ResourceExport:
    """Codec output before package-level sealing and manifest construction."""

    resource_id: str
    resource_version: str
    payload: dict[str, JsonValue]
    id_policy: IdPolicy = IdPolicy.PRESERVE
    dependencies: tuple[DependencyRequirement, ...] = ()


class ResourceCodec(Protocol):
    """Replaceable mapping between one canonical resource type and portable JSON."""

    @property
    def resource_type(self) -> str: ...

    def serialize(self, value: object) -> ResourceExport: ...

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object: ...


class ResourceSerializerRegistry:
    """Registry of explicit resource codecs; no implicit Python-object serialization."""

    def __init__(self) -> None:
        self._codecs: dict[str, ResourceCodec] = {}

    def register(self, codec: ResourceCodec) -> None:
        resource_type = codec.resource_type.strip()
        if not resource_type:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable resource codec type must not be blank",
            )
        if resource_type in self._codecs:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"portable resource codec already registered: {resource_type}",
            )
        self._codecs[resource_type] = codec

    def resource_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._codecs))

    def serialize(self, resource_type: str, value: object) -> PortableResource:
        codec = self._get(resource_type)
        exported = codec.serialize(value)
        return seal_resource(
            PortableResource(
                resource_type=resource_type,
                resource_id=exported.resource_id,
                resource_version=exported.resource_version,
                payload=exported.payload,
                id_policy=exported.id_policy,
                dependencies=exported.dependencies,
            )
        )

    def deserialize(
        self,
        resource: PortableResource,
        context: ImportContext | None = None,
    ) -> object:
        verify_resource(resource)
        codec = self._get(resource.resource_type)
        return codec.deserialize(resource, context or ImportContext())

    def _get(self, resource_type: str) -> ResourceCodec:
        try:
            return self._codecs[resource_type]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"portable resource codec not registered: {resource_type}",
            ) from exc
