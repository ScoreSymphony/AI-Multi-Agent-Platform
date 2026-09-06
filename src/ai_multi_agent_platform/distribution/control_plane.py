"""Optional northbound Control Plane seam for registry discovery and activation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .items import RegistryItem, RegistryQuery
from .service import DistributionPreview, DistributionService
from .validation import ValidationContext

REGISTRY_COLLECTION = "registry-items"
REGISTRY_PREVIEW_COMMAND = "registry.preview"
REGISTRY_ACTIVATE_COMMAND = "registry.activate"


class RegistryValidationContextResolver(Protocol):
    """Resolve validation inputs from authoritative server-side platform state."""

    async def resolve(self, context: RequestContext) -> ValidationContext: ...


class RegistryResourceService:
    """Read-only provider-neutral Registry metadata exposed through the Control Plane."""

    search_indexable = False

    def __init__(self, distribution: DistributionService) -> None:
        self.distribution = distribution

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_item_resource(item) for item in self.distribution.search(RegistryQuery()))

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        item_id, version = _split_resource_id(resource_id)
        try:
            return _item_resource(self.distribution.get(item_id, version))
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc


class RegistryCommandHandlers:
    """Preview and activation commands behind the existing Control Plane auth boundary."""

    def __init__(
        self,
        distribution: DistributionService,
        validation_context_resolver: RegistryValidationContextResolver,
    ) -> None:
        self.distribution = distribution
        self.validation_context_resolver = validation_context_resolver

    async def preview(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        version = _required_version(payload, "registry.preview")
        validation_context = await self.validation_context_resolver.resolve(context)
        try:
            preview = self.distribution.preview(resource_ref, version, validation_context)
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        return _preview_resource(preview)

    async def activate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        version = _required_version(payload, "registry.activate")
        validation_context = await self.validation_context_resolver.resolve(context)
        try:
            preview = self.distribution.preview(resource_ref, version, validation_context)
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        if not preview.activation_allowed:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry item does not pass activation validation",
            )
        try:
            await self.distribution.activate(preview, validation_context, authorized=True)
        except ContractError:
            raise
        except PermissionError as exc:
            raise ContractError(ErrorCode.FORBIDDEN, str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
        return {
            "id": f"{preview.item.item_id}@{preview.item.version}",
            "type": "registry-activation",
            "status": "applied",
            "route": preview.route.value,
        }


def register_distribution_control_plane(
    control_plane: ControlPlane,
    distribution: DistributionService,
    *,
    validation_context_resolver: RegistryValidationContextResolver | None = None,
) -> None:
    """Register #81 only when a Registry provider is configured.

    Discovery remains read-only. Preview and activation are registered only when the
    deployment can resolve authoritative validation state server-side. Activation is
    additionally exposed only when a canonical owner-domain router is configured. The
    generic Control Plane performs #15 authorization before either command executes.
    """

    if not distribution.enabled:
        return
    control_plane.register_resource_service(
        REGISTRY_COLLECTION,
        RegistryResourceService(distribution),
    )
    if validation_context_resolver is not None:
        handlers = RegistryCommandHandlers(distribution, validation_context_resolver)
        control_plane.register_command(REGISTRY_PREVIEW_COMMAND, handlers.preview)
        if distribution.activation_enabled:
            control_plane.register_command(REGISTRY_ACTIVATE_COMMAND, handlers.activate)


def _required_version(payload: dict[str, JsonValue], command: str) -> str:
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{command} requires version")
    return version


def _split_resource_id(resource_id: str) -> tuple[str, str | None]:
    item_id, separator, version = resource_id.rpartition("@")
    if not separator:
        return resource_id, None
    if not item_id or not version:
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid registry resource id")
    return item_id, version


def _json_strings(values: Iterable[str]) -> list[JsonValue]:
    return [cast(JsonValue, value) for value in values]


def _item_resource(item: RegistryItem) -> dict[str, JsonValue]:
    dependencies: list[JsonValue] = [
        {
            "item_id": dependency.item_id,
            "minimum_version": dependency.version_range.minimum,
            "maximum_version": dependency.version_range.maximum,
            "optional": dependency.optional,
        }
        for dependency in item.dependencies
    ]
    return {
        "id": f"{item.item_id}@{item.version}",
        "type": "registry-item",
        "item_id": item.item_id,
        "item_type": item.item_type.value,
        "name": item.name,
        "description": item.description,
        "version": item.version,
        "publisher": item.publisher,
        "source": {
            "repository": item.source.repository,
            "package_reference": item.source.package_reference,
            "revision": item.source.revision,
        },
        "license": item.license,
        "provenance": item.provenance,
        "minimum_platform_version": item.supported_platform.minimum,
        "maximum_platform_version": item.supported_platform.maximum,
        "dependencies": dependencies,
        "requested_permissions": _json_strings(sorted(item.requested_permissions)),
        "required_capabilities": _json_strings(sorted(item.required_capabilities)),
        "required_plugins": _json_strings(item.required_plugins),
        "required_connectors": _json_strings(item.required_connectors),
        "required_models": _json_strings(item.required_models),
        "tags": _json_strings(sorted(item.tags)),
        "categories": _json_strings(sorted(item.categories)),
        "trust_status": item.trust_status.value,
        "review_reference": item.review_reference,
        "released_at": item.released_at,
        "changelog": item.changelog,
        "deprecated": item.deprecated,
        "yanked": item.yanked,
        "route": item.route.value,
        "integrity": {
            "sha256": item.integrity.sha256,
            "signature_present": item.integrity.signature is not None,
            "signature_key_id": item.integrity.signature_key_id,
        },
    }


def _preview_resource(preview: DistributionPreview) -> dict[str, JsonValue]:
    return {
        "id": f"{preview.item.item_id}@{preview.item.version}",
        "type": "registry-preview",
        "provider_id": preview.provider_id,
        "item": _item_resource(preview.item),
        "route": preview.route.value,
        "activation_allowed": preview.activation_allowed,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity.value,
                "message": finding.message,
            }
            for finding in preview.findings
        ],
    }
