"""Optional northbound Control Plane seam for Registry/Marketplace operations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .items import RegistryItem, RegistryQuery
from .models import RegistryItemType, TrustStatus
from .service import DistributionPreview, DistributionService
from .state import RegistryInstallation
from .validation import ValidationContext

REGISTRY_COLLECTION = "registry-items"
REGISTRY_PREVIEW_COMMAND = "registry.preview"
REGISTRY_ACTIVATE_COMMAND = "registry.activate"
REGISTRY_PIN_COMMAND = "registry.pin"
REGISTRY_UNPIN_COMMAND = "registry.unpin"


class RegistryValidationContextResolver(Protocol):
    """Resolve validation inputs from authoritative server-side platform state."""

    async def resolve(self, context: RequestContext) -> ValidationContext: ...


class RegistryResourceService:
    """Read-only provider-neutral Registry metadata exposed through the Control Plane."""

    search_indexable = False
    handles_search_and_filters = True

    def __init__(self, distribution: DistributionService) -> None:
        self.distribution = distribution

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        registry_query, update_available = _registry_query(query)
        items = self.distribution.search(registry_query)
        resources: list[dict[str, JsonValue]] = []
        for item in items:
            installation = self.distribution.installed(item.item_id)
            has_update = _is_update(item, installation)
            if update_available is not None and has_update is not update_available:
                continue
            resources.append(_item_resource(item, installation, update_available=has_update))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        item_id, version = _split_resource_id(resource_id)
        try:
            item = self.distribution.get(item_id, version)
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        installation = self.distribution.installed(item.item_id)
        return _item_resource(item, installation, update_available=_is_update(item, installation))


class RegistryCommandHandlers:
    """Preview, activation and pin commands behind the existing Control Plane auth boundary."""

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
        return _preview_resource(preview, self.distribution.installed(resource_ref))

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
        installation = self.distribution.installed(resource_ref)
        return {
            "id": f"{preview.item.item_id}@{preview.item.version}",
            "type": "registry-activation",
            "status": "applied",
            "route": preview.route.value,
            "installation": _installation_resource(installation) if installation else None,
        }

    async def pin(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        version = _required_version(payload, "registry.pin")
        try:
            installation = self.distribution.pin(resource_ref, version)
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
        return _installation_resource(installation)

    async def unpin(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        if payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "registry.unpin accepts no payload fields"
            )
        try:
            installation = self.distribution.unpin(resource_ref)
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except RuntimeError as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
        return _installation_resource(installation)


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
    generic Control Plane performs #15 authorization before commands execute.
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
        if distribution.installation_state_enabled:
            control_plane.register_command(REGISTRY_PIN_COMMAND, handlers.pin)
            control_plane.register_command(REGISTRY_UNPIN_COMMAND, handlers.unpin)


def _registry_query(query: PageQuery) -> tuple[RegistryQuery, bool | None]:
    filters = dict(query.filters or {})
    item_types = frozenset(
        RegistryItemType(value) for value in _values(filters.pop("item_type", None))
    )
    tags = frozenset(_values(filters.pop("tag", None)))
    categories = frozenset(_values(filters.pop("category", None)))
    licenses = frozenset(_values(filters.pop("license", None)))
    publishers = frozenset(_values(filters.pop("publisher", None)))
    capabilities = frozenset(_values(filters.pop("required_capability", None)))
    trust_statuses = frozenset(
        TrustStatus(value) for value in _values(filters.pop("trust_status", None))
    )
    platform_version = filters.pop("platform_version", None)
    update_for_item_id = filters.pop("update_for_item_id", None)
    include_deprecated = bool(
        _optional_bool(filters.pop("include_deprecated", None), default=False)
    )
    include_yanked = bool(_optional_bool(filters.pop("include_yanked", None), default=False))
    update_available = _optional_bool(filters.pop("update_available", None), default=None)
    if filters:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported Registry filter(s): " + ", ".join(sorted(filters)),
        )
    try:
        return (
            RegistryQuery(
                text=query.search,
                item_types=item_types,
                tags=tags,
                categories=categories,
                licenses=licenses,
                publishers=publishers,
                required_capabilities=capabilities,
                trust_statuses=trust_statuses,
                platform_version=platform_version,
                include_deprecated=include_deprecated,
                include_yanked=include_yanked,
                update_for_item_id=update_for_item_id,
            ),
            update_available,
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _values(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    values = tuple(part.strip() for part in value.split(",") if part.strip())
    if not values:
        raise ContractError(ErrorCode.INVALID_REQUEST, "Registry filter value must be non-blank")
    return values


def _optional_bool(value: str | None, *, default: bool | None) -> bool | None:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ContractError(ErrorCode.INVALID_REQUEST, "Registry boolean filter must be true or false")


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


def _is_update(item: RegistryItem, installation: RegistryInstallation | None) -> bool:
    return installation is not None and installation.as_installed().accepts_update(item)


def _item_resource(
    item: RegistryItem,
    installation: RegistryInstallation | None = None,
    *,
    update_available: bool = False,
) -> dict[str, JsonValue]:
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
        "installed": installation is not None,
        "installed_version": installation.current.version if installation else None,
        "pinned_version": installation.pinned_version if installation else None,
        "update_available": update_available,
        "installation": _installation_resource(installation) if installation else None,
    }


def _installation_resource(installation: RegistryInstallation) -> dict[str, JsonValue]:
    current = installation.current
    history: list[JsonValue] = [
        {
            "version": snapshot.version,
            "source_registry": snapshot.source_registry,
            "source_repository": snapshot.source_repository,
            "package_reference": snapshot.package_reference,
            "revision": snapshot.revision,
            "license": snapshot.license,
            "provenance": snapshot.provenance,
        }
        for snapshot in installation.history
    ]
    return {
        "id": current.item_id,
        "type": "registry-installation",
        "item_id": current.item_id,
        "version": current.version,
        "pinned_version": installation.pinned_version,
        "source_registry": current.source_registry,
        "source_repository": current.source_repository,
        "package_reference": current.package_reference,
        "revision": current.revision,
        "license": current.license,
        "provenance": current.provenance,
        "history": history,
    }


def _preview_resource(
    preview: DistributionPreview,
    installation: RegistryInstallation | None = None,
) -> dict[str, JsonValue]:
    return {
        "id": f"{preview.item.item_id}@{preview.item.version}",
        "type": "registry-preview",
        "provider_id": preview.provider_id,
        "item": _item_resource(
            preview.item,
            installation,
            update_available=_is_update(preview.item, installation),
        ),
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
