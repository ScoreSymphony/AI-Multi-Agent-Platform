"""Optional northbound Control Plane seam for Registry/Marketplace operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_value

from .dependency_graph import evaluate_compatibility
from .control_plane_projection import (
    _decision_resource,
    _installation_resource,
    _is_update,
    _item_resource,
    _json_strings,
    _marketplace_mutation_resource,
    _preview_resource,
)
from .items import RegistryItem, RegistryQuery
from .models import DistributionRoute, TrustStatus, version_key
from .provider import RegistrySourceConflictError, RegistryUnavailableError
from .service import (
    DistributionPreview,
    DistributionService,
    DistributionUninstallPreview,
)
from .validation import ValidationContext

REGISTRY_COLLECTION = "registry-items"
REGISTRY_PREVIEW_COMMAND = "registry.preview"
REGISTRY_ACTIVATE_COMMAND = "registry.activate"
REGISTRY_PIN_COMMAND = "registry.pin"
REGISTRY_UNPIN_COMMAND = "registry.unpin"
MARKETPLACE_PREVIEW_COMMAND = "marketplace.preview"
MARKETPLACE_INSTALL_COMMAND = "marketplace.install"
MARKETPLACE_UPDATE_COMMAND = "marketplace.update"
MARKETPLACE_UNINSTALL_COMMAND = "marketplace.uninstall"

_MARKETPLACE_SORT_FIELDS = frozenset(
    {
        "id",
        "item_id",
        "item_type",
        "kind",
        "name",
        "description",
        "version",
        "publisher",
        "source_registry",
        "source",
        "license",
        "provenance",
        "minimum_platform_version",
        "maximum_platform_version",
        "trust_status",
        "trust",
        "review_reference",
        "released_at",
        "release_date",
        "changelog",
        "deprecated",
        "yanked",
        "route",
        "route_available",
        "installed",
        "installed_version",
        "pinned_version",
        "update_available",
    }
)


class RegistryValidationContextResolver(Protocol):
    """Resolve validation inputs from authoritative server-side platform state."""

    async def resolve(self, context: RequestContext) -> ValidationContext: ...


@dataclass(frozen=True, slots=True)
class _RegistryQueryPlan:
    query: RegistryQuery
    sources: frozenset[str] = frozenset()
    installed: bool | None = None
    update_available: bool | None = None
    deprecated: bool | None = None
    yanked: bool | None = None
    compatible: bool | None = None
    compatibility_platform_version: str | None = None


class RegistryResourceService:
    """Unified provider-neutral Marketplace metadata exposed through the Control Plane."""

    search_indexable = False
    handles_search_and_filters = True
    handles_sorting = True

    def __init__(
        self,
        distribution: DistributionService,
        validation_context_resolver: RegistryValidationContextResolver | None = None,
    ) -> None:
        self.distribution = distribution
        self.validation_context_resolver = validation_context_resolver

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        _validate_marketplace_sort(query.sort)
        plan = _registry_query(query)
        compatibility_version = plan.compatibility_platform_version
        compatibility_context: ValidationContext | None = None
        if plan.compatible is not None:
            if self.validation_context_resolver is not None:
                compatibility_context = await self.validation_context_resolver.resolve(context)
                if compatibility_version is not None:
                    compatibility_context = replace(
                        compatibility_context,
                        platform_version=compatibility_version,
                    )
                else:
                    compatibility_version = compatibility_context.platform_version
            elif compatibility_version is None:
                compatibility_version = await self._current_platform_version(context)
        try:
            items = self.distribution.search(plan.query)
        except RegistryUnavailableError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "marketplace provider is unavailable",
                retryable=True,
                details={"marketplace_reason": "provider_failure"},
            ) from exc
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "marketplace provider returned invalid metadata",
                details={"marketplace_reason": "provider_failure"},
            ) from exc

        resources: list[dict[str, JsonValue]] = []
        for item in items:
            if plan.sources and item.source_registry not in plan.sources:
                continue
            installation = self.distribution.installed(item.item_id)
            has_update = _is_update(item, installation)
            is_installed = installation is not None
            compatibility_decision = (
                evaluate_compatibility(item, compatibility_context)
                if compatibility_context is not None
                else None
            )
            is_compatible = (
                compatibility_decision.compatible
                if compatibility_decision is not None
                else (
                    item.supported_platform.contains(compatibility_version)
                    if compatibility_version is not None
                    else None
                )
            )
            if plan.installed is not None and is_installed is not plan.installed:
                continue
            if plan.update_available is not None and has_update is not plan.update_available:
                continue
            if plan.deprecated is not None and item.deprecated is not plan.deprecated:
                continue
            if plan.yanked is not None and item.yanked is not plan.yanked:
                continue
            if plan.compatible is not None and is_compatible is not plan.compatible:
                continue
            resources.append(
                _item_resource(
                    item,
                    installation,
                    update_available=has_update,
                    platform_compatible=(
                        compatibility_decision.platform_compatible
                        if compatibility_decision is not None
                        else is_compatible
                    ),
                    compatibility_decision=compatibility_decision,
                    route_available=self.distribution.route_available(item),
                )
            )
        resources.sort(
            key=lambda resource: _marketplace_resource_sort_key(resource, query.sort),
            reverse=query.direction == "desc",
        )
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        item_id, version, source_registry = _split_resource_id(resource_id)
        try:
            item = self.distribution.get(
                item_id,
                version,
                source_registry=source_registry,
            )
        except RegistrySourceConflictError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                str(exc),
                details={"marketplace_reason": "source_ambiguous"},
            ) from exc
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except RegistryUnavailableError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "marketplace provider is unavailable",
                retryable=True,
                details={"marketplace_reason": "provider_failure"},
            ) from exc
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "marketplace provider returned invalid metadata",
                details={"marketplace_reason": "provider_failure"},
            ) from exc

        installation = self.distribution.installed(item.item_id)
        validation_context = await self._optional_validation_context(context)
        platform_version = (
            validation_context.platform_version if validation_context is not None else None
        )
        compatibility_decision = (
            evaluate_compatibility(item, validation_context)
            if validation_context is not None
            else None
        )
        requirements: object | None = None
        owner_details: object | None = None
        owner_status: object | None = None
        owner_status_version: str | None = None
        if item.route is DistributionRoute.KIND_HANDLER:
            try:
                requirements = self.distribution.inspect_requirements(item)
                owner_details = self.distribution.describe(item)
                if installation is not None:
                    status_item: RegistryItem | None = item
                    if installation.current.version != item.version:
                        try:
                            status_item = self.distribution.get(
                                item.item_id,
                                installation.current.version,
                                source_registry=installation.current.source_registry,
                            )
                        except LookupError:
                            status_item = None
                    if status_item is not None:
                        owner_status = self.distribution.status(status_item)
                        owner_status_version = status_item.version
            except ContractError:
                raise
            # error-boundary: allow-broad-catch=translation reviewed owner detail translation
            except Exception as exc:
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    "marketplace owner detail provider failed",
                    details={"marketplace_reason": "owner_failure", "kind": item.kind},
                ) from exc
        serialized_status = json_value(owner_status) if owner_status is not None else None
        return _item_resource(
            item,
            installation,
            update_available=_is_update(item, installation),
            platform_compatible=(
                compatibility_decision.platform_compatible
                if compatibility_decision is not None
                else (
                    item.supported_platform.contains(platform_version)
                    if platform_version is not None
                    else None
                )
            ),
            compatibility_decision=compatibility_decision,
            route_available=self.distribution.route_available(item),
            owner_extension={
                "handler_available": self.distribution.has_kind_handler(item),
                "requirements": json_value(requirements) if requirements is not None else None,
                "details": json_value(owner_details) if owner_details is not None else None,
                "status": serialized_status,
                "status_version": owner_status_version,
            }
            if item.route is DistributionRoute.KIND_HANDLER
            else None,
        )

    async def _current_platform_version(self, context: RequestContext) -> str:
        if self.validation_context_resolver is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "compatible filter requires platform_version when no validation resolver "
                "is configured",
                details={"field": "platform_version"},
            )
        validation = await self.validation_context_resolver.resolve(context)
        return validation.platform_version

    async def _optional_validation_context(
        self,
        context: RequestContext,
    ) -> ValidationContext | None:
        if self.validation_context_resolver is None:
            return None
        return await self.validation_context_resolver.resolve(context)


class RegistryCommandHandlers:
    """Registry compatibility commands plus explicit Marketplace lifecycle actions."""

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
        version = _required_version(payload, REGISTRY_PREVIEW_COMMAND)
        source_registry = _optional_source_registry(payload)
        _, preview = await self._resolve_preview(
            context,
            resource_ref,
            version,
            source_registry=source_registry,
        )
        return _preview_resource(preview, self.distribution.installed(resource_ref))

    async def activate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Backward-compatible Registry activation: install or update as state requires."""

        version = _required_version(payload, REGISTRY_ACTIVATE_COMMAND)
        source_registry = _optional_source_registry(payload)
        validation_context, preview = await self._resolve_preview(
            context,
            resource_ref,
            version,
            source_registry=source_registry,
        )
        if not preview.activation_allowed:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry item does not pass activation validation",
            )
        await self._activate_owner(preview, validation_context, action="activate")
        installation = self.distribution.installed(resource_ref)
        return {
            "id": f"{preview.item.item_id}@{preview.item.version}",
            "type": "registry-activation",
            "status": "applied",
            "route": preview.route.value,
            "installation": _installation_resource(installation) if installation else None,
        }

    async def marketplace_preview(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        version = _required_version(payload, MARKETPLACE_PREVIEW_COMMAND)
        source_registry = _optional_source_registry(payload)
        _, preview = await self._resolve_preview(
            context,
            resource_ref,
            version,
            source_registry=source_registry,
        )
        route_available = self.distribution.route_available(preview.item)
        return _preview_resource(
            preview,
            self.distribution.installed(resource_ref),
            route_available=route_available,
            activation_allowed=preview.activation_allowed and route_available,
            include_decision=True,
        )

    async def marketplace_install(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        version = _required_version(payload, MARKETPLACE_INSTALL_COMMAND)
        source_registry = _optional_source_registry(payload)
        validation_context, preview = await self._resolve_preview(
            context,
            resource_ref,
            version,
            source_registry=source_registry,
        )
        if self.distribution.installed(resource_ref) is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "marketplace item is already installed; use marketplace.update",
                details={"marketplace_reason": "already_installed"},
            )
        _require_marketplace_activation(
            preview,
            route_available=self.distribution.route_available(preview.item),
        )
        await self._activate_owner(preview, validation_context, action="install")
        return _marketplace_mutation_resource(
            "install", preview, self.distribution.installed(resource_ref)
        )

    async def marketplace_update(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        version = _required_version(payload, MARKETPLACE_UPDATE_COMMAND)
        source_registry = _optional_source_registry(payload)
        validation_context, preview = await self._resolve_preview(
            context,
            resource_ref,
            version,
            source_registry=source_registry,
        )
        installation = self.distribution.installed(resource_ref)
        if installation is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "marketplace item is not installed; use marketplace.install",
                details={"marketplace_reason": "not_installed"},
            )
        if version_key(preview.item.version) <= version_key(installation.current.version):
            raise ContractError(
                ErrorCode.CONFLICT,
                "marketplace update version must be newer than the installed version",
                details={
                    "marketplace_reason": "not_newer",
                    "installed_version": installation.current.version,
                },
            )
        _require_marketplace_activation(
            preview,
            route_available=self.distribution.route_available(preview.item),
        )
        await self._activate_owner(preview, validation_context, action="update")
        return _marketplace_mutation_resource(
            "update", preview, self.distribution.installed(resource_ref)
        )

    async def marketplace_uninstall(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        if payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "marketplace.uninstall accepts no payload fields",
            )
        try:
            preview = self.distribution.preview_uninstall(resource_ref)
        except RegistrySourceConflictError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                str(exc),
                details={"marketplace_reason": "source_ambiguous"},
            ) from exc
        except LookupError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                str(exc),
                details={"marketplace_reason": "not_installed"},
            ) from exc
        except RegistryUnavailableError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "marketplace provider is unavailable",
                retryable=True,
                details={"marketplace_reason": "provider_failure"},
            ) from exc
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "marketplace provider returned invalid uninstall metadata",
                details={"marketplace_reason": "provider_failure"},
            ) from exc

        if preview.item.route is not DistributionRoute.KIND_HANDLER:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "marketplace uninstall is not available for this component route",
                details={
                    "marketplace_reason": "uninstall_not_supported",
                    "route": preview.item.route.value,
                },
            )
        if not self.distribution.has_kind_handler(preview.item):
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "marketplace owner handler is unavailable",
                details={
                    "marketplace_reason": "missing_handler",
                    "kind": preview.item.kind,
                },
            )
        _require_marketplace_uninstall(preview)
        try:
            await self.distribution.uninstall(preview, authorized=True)
        except ContractError:
            raise
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "marketplace owner handler is unavailable",
                details={
                    "marketplace_reason": "missing_handler",
                    "kind": preview.item.kind,
                },
            ) from exc
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except PermissionError as exc:
            raise ContractError(ErrorCode.FORBIDDEN, str(exc)) from exc
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                "marketplace uninstall failed revalidation",
                details={"marketplace_reason": "dependency_block"},
            ) from exc
        except RuntimeError as exc:
            if str(exc) in {
                "registry provider changed after preview",
                "registry metadata changed after preview",
                "installed registry state changed after preview",
                "registry decision state changed after preview",
            }:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "marketplace uninstall preview no longer matches current state",
                    details={"marketplace_reason": "preview_drift"},
                ) from exc
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "marketplace owner uninstall failed",
                details={
                    "marketplace_reason": "owner_failure",
                    "kind": preview.item.kind,
                },
            ) from exc
        # error-boundary: allow-broad-catch=translation reviewed owner uninstall translation
        except Exception as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "marketplace owner uninstall failed",
                details={
                    "marketplace_reason": "owner_failure",
                    "kind": preview.item.kind,
                },
            ) from exc
        return {
            "id": resource_ref,
            "type": "marketplace-mutation",
            "action": "uninstall",
            "status": "applied",
            "route": preview.item.route.value,
            "decision": _decision_resource(preview.decision),
            "installation": None,
        }

    async def pin(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        version = _required_version(payload, REGISTRY_PIN_COMMAND)
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

    async def _resolve_preview(
        self,
        context: RequestContext,
        resource_ref: str,
        version: str,
        *,
        source_registry: str | None = None,
    ) -> tuple[ValidationContext, DistributionPreview]:
        validation_context = await self.validation_context_resolver.resolve(context)
        try:
            preview = self.distribution.preview(
                resource_ref,
                version,
                validation_context,
                source_registry=source_registry,
            )
        except RegistrySourceConflictError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                str(exc),
                details={"marketplace_reason": "source_ambiguous"},
            ) from exc
        except LookupError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except RegistryUnavailableError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "marketplace provider is unavailable",
                retryable=True,
                details={"marketplace_reason": "provider_failure"},
            ) from exc
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "marketplace provider returned invalid metadata or artifact",
                details={"marketplace_reason": "provider_failure"},
            ) from exc
        return validation_context, preview

    async def _activate_owner(
        self,
        preview: DistributionPreview,
        validation_context: ValidationContext,
        *,
        action: str,
    ) -> None:
        try:
            await self.distribution.activate(preview, validation_context, authorized=True)
        except ContractError:
            raise
        except PermissionError as exc:
            raise ContractError(ErrorCode.FORBIDDEN, str(exc)) from exc
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "marketplace owner handler is unavailable",
                details={
                    "marketplace_reason": "missing_handler",
                    "kind": preview.item.kind,
                },
            ) from exc
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"marketplace {action} failed revalidation",
                details={"marketplace_reason": "validation_block"},
            ) from exc
        except RuntimeError as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"marketplace owner {action} failed",
                details={
                    "marketplace_reason": "owner_failure",
                    "kind": preview.item.kind,
                },
            ) from exc
        # error-boundary: allow-broad-catch=translation reviewed owner mutation translation
        except Exception as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"marketplace owner {action} failed",
                details={
                    "marketplace_reason": "owner_failure",
                    "kind": preview.item.kind,
                },
            ) from exc


def register_distribution_control_plane(
    control_plane: ControlPlane,
    distribution: DistributionService,
    *,
    validation_context_resolver: RegistryValidationContextResolver | None = None,
) -> None:
    """Register the Registry/Marketplace surface only when a provider is configured.

    Discovery remains read-only. Preview and lifecycle commands require authoritative
    validation state; explicit Marketplace mutations also require durable installation
    state and fail closed when their canonical owner route is unavailable. Legacy
    registry.activate retains its existing owner-router availability contract. The
    generic Control Plane performs authorization before commands execute.
    """

    if not distribution.enabled:
        return
    control_plane.register_resource_service(
        REGISTRY_COLLECTION,
        RegistryResourceService(distribution, validation_context_resolver),
    )
    if validation_context_resolver is not None:
        handlers = RegistryCommandHandlers(distribution, validation_context_resolver)
        control_plane.register_command(REGISTRY_PREVIEW_COMMAND, handlers.preview)
        control_plane.register_command(MARKETPLACE_PREVIEW_COMMAND, handlers.marketplace_preview)
        if distribution.activation_enabled:
            control_plane.register_command(REGISTRY_ACTIVATE_COMMAND, handlers.activate)
        if distribution.installation_state_enabled:
            control_plane.register_command(
                MARKETPLACE_INSTALL_COMMAND, handlers.marketplace_install
            )
            control_plane.register_command(MARKETPLACE_UPDATE_COMMAND, handlers.marketplace_update)
            control_plane.register_command(
                MARKETPLACE_UNINSTALL_COMMAND, handlers.marketplace_uninstall
            )
            control_plane.register_command(REGISTRY_PIN_COMMAND, handlers.pin)
            control_plane.register_command(REGISTRY_UNPIN_COMMAND, handlers.unpin)


def _registry_query(query: PageQuery) -> _RegistryQueryPlan:
    filters = dict(query.filters or {})
    item_types = frozenset(_merge_filter_values(filters, "item_type", "kind"))
    tags = frozenset(_values(filters.pop("tag", None)))
    categories = frozenset(_values(filters.pop("category", None)))
    licenses = frozenset(_values(filters.pop("license", None)))
    publishers = frozenset(_values(filters.pop("publisher", None)))
    sources = frozenset(_merge_filter_values(filters, "source_registry", "source"))
    capabilities = frozenset(_values(filters.pop("required_capability", None)))
    raw_trust = _merge_filter_values(filters, "trust_status", "trust")
    try:
        trust_statuses = frozenset(TrustStatus(value) for value in raw_trust)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "invalid Marketplace trust status",
            details={"field": "trust"},
        ) from exc

    platform_version = filters.pop("platform_version", None)
    update_for_item_id = filters.pop("update_for_item_id", None)
    installed = _optional_bool(filters.pop("installed", None), default=None)
    update_available = _optional_bool(filters.pop("update_available", None), default=None)
    deprecated = _optional_bool(filters.pop("deprecated", None), default=None)
    yanked = _optional_bool(filters.pop("yanked", None), default=None)
    compatible = _optional_bool(filters.pop("compatible", None), default=None)
    include_deprecated = bool(
        _optional_bool(filters.pop("include_deprecated", None), default=False)
    )
    include_yanked = bool(_optional_bool(filters.pop("include_yanked", None), default=False))
    technical_only = bool(_optional_bool(filters.pop("technical_component", None), default=False))
    if deprecated is not None:
        include_deprecated = True
    if yanked is not None:
        include_yanked = True
    if filters:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported Marketplace filter(s): " + ", ".join(sorted(filters)),
        )
    try:
        registry_query = RegistryQuery(
            text=query.search,
            item_types=item_types,
            tags=tags,
            categories=categories,
            licenses=licenses,
            publishers=publishers,
            required_capabilities=capabilities,
            trust_statuses=trust_statuses,
            platform_version=platform_version if compatible is not False else None,
            include_deprecated=include_deprecated,
            include_yanked=include_yanked,
            update_for_item_id=update_for_item_id,
            technical_only=technical_only,
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
    return _RegistryQueryPlan(
        query=registry_query,
        sources=sources,
        installed=installed,
        update_available=update_available,
        deprecated=deprecated,
        yanked=yanked,
        compatible=compatible,
        compatibility_platform_version=platform_version,
    )


def _merge_filter_values(
    filters: dict[str, str],
    legacy_name: str,
    marketplace_name: str,
) -> tuple[str, ...]:
    values = [
        *_values(filters.pop(legacy_name, None)),
        *_values(filters.pop(marketplace_name, None)),
    ]
    return tuple(dict.fromkeys(values))


def _validate_marketplace_sort(sort: str) -> None:
    if sort not in _MARKETPLACE_SORT_FIELDS:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported Marketplace sort field",
            details={
                "field": "sort",
                "supported": _json_strings(sorted(_MARKETPLACE_SORT_FIELDS)),
            },
        )


def _marketplace_resource_sort_key(
    resource: dict[str, JsonValue],
    sort: str,
) -> tuple[object, str]:
    canonical_sort = {
        "kind": "kind",
        "item_type": "kind",
        "release_date": "released_at",
        "source": "source_registry",
    }.get(sort, sort)
    raw = resource.get(canonical_sort)
    if canonical_sort == "version":
        primary: object = version_key(str(raw))
    elif isinstance(raw, str):
        primary = raw.casefold()
    elif raw is None:
        primary = ""
    else:
        primary = str(raw)
    return primary, str(resource.get("qualified_id") or resource.get("id", ""))


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


def _optional_source_registry(payload: dict[str, JsonValue]) -> str | None:
    value = payload.get("source_registry")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "source_registry must be a non-blank string",
            details={"field": "source_registry"},
        )
    return value.strip()


def _split_resource_id(resource_id: str) -> tuple[str, str | None, str | None]:
    source_registry: str | None = None
    source, source_separator, unqualified = resource_id.partition("::")
    if source_separator:
        if not source.strip() or not unqualified:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid registry resource id")
        source_registry = source.strip()
        resource_id = unqualified
    item_id, separator, version = resource_id.rpartition("@")
    if not separator:
        return resource_id, None, source_registry
    if not item_id or not version:
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid registry resource id")
    return item_id, version, source_registry


def _require_marketplace_activation(
    preview: DistributionPreview,
    *,
    route_available: bool,
) -> None:
    if preview.route is DistributionRoute.MANUAL:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "marketplace item requires manual installation",
            details={"marketplace_reason": "manual_route"},
        )

    errors = tuple(finding for finding in preview.findings if finding.severity.value == "error")
    error_codes = {finding.code for finding in errors}
    categories = {finding.category.value for finding in errors}
    if "integrity" in categories:
        code = ErrorCode.PERMANENT_FAILURE
        reason = "integrity_failure"
    elif {"permission", "policy"} & categories:
        code = ErrorCode.FORBIDDEN
        reason = "policy_block"
    elif "compatibility" in categories:
        code = ErrorCode.NO_COMPATIBLE_ROUTE
        reason = "compatibility_block"
    elif "dependency" in categories:
        code = ErrorCode.CONFLICT
        reason = "dependency_block"
    elif errors:
        code = ErrorCode.CONFLICT
        reason = "validation_block"
    elif not route_available:
        code = ErrorCode.UNSUPPORTED_CAPABILITY
        reason = (
            "missing_handler"
            if preview.route is DistributionRoute.KIND_HANDLER
            else "route_unavailable"
        )
    elif preview.activation_allowed:
        return
    else:
        code = ErrorCode.CONFLICT
        reason = "validation_block"
    raise ContractError(
        code,
        "marketplace item is not installable in the current environment",
        details={
            "marketplace_reason": reason,
            "findings": _json_strings(sorted(error_codes)),
            "kind": preview.item.kind,
            "route": preview.route.value,
        },
    )


def _require_marketplace_uninstall(preview: DistributionUninstallPreview) -> None:
    errors = tuple(finding for finding in preview.findings if finding.severity.value == "error")
    if preview.activation_allowed and not errors:
        return
    raise ContractError(
        ErrorCode.CONFLICT,
        "marketplace item cannot be uninstalled while required by installed components",
        details={
            "marketplace_reason": "dependency_block",
            "findings": _json_strings(sorted(finding.code for finding in errors)),
            "kind": preview.item.kind,
            "route": preview.item.route.value,
        },
    )
