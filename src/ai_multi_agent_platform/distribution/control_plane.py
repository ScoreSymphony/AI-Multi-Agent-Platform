"""Optional northbound Control Plane seam for Registry/Marketplace operations."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_value

from .control_plane_projection import (
    _decision_resource,
    _installation_resource,
    _is_update,
    _item_resource,
    _json_strings,
    _marketplace_mutation_resource,
    _preview_resource,
)
from .control_plane_query import RegistryQueryPlan as _RegistryQueryPlan
from .control_plane_query import (
    marketplace_resource_sort_key as _marketplace_resource_sort_key,
)
from .control_plane_query import optional_source_registry as _optional_source_registry
from .control_plane_query import registry_query as _registry_query
from .control_plane_query import required_version as _required_version
from .control_plane_query import split_resource_id as _split_resource_id
from .control_plane_query import validate_marketplace_sort as _validate_marketplace_sort
from .dependency_graph import evaluate_compatibility
from .items import RegistryItem, RegistryQuery
from .models import DistributionRoute, version_key
from .provider import RegistrySourceConflictError, RegistryUnavailableError
from .service import (
    DistributionPreview,
    DistributionService,
    DistributionUninstallPreview,
)
from .state import RegistryInstallation
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


class RegistryValidationContextResolver(Protocol):
    """Resolve validation inputs from authoritative server-side platform state."""

    async def resolve(self, context: RequestContext) -> ValidationContext: ...


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
        compatibility_context, compatibility_version = await self._list_compatibility_context(
            context, plan
        )
        items = self._search_items(plan.query)
        resources = [
            resource
            for item in items
            if (
                resource := self._list_item_resource(
                    item,
                    plan,
                    compatibility_context,
                    compatibility_version,
                )
            )
            is not None
        ]
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
        item = self._get_item(item_id, version, source_registry=source_registry)
        installation = self.distribution.installed(item.item_id)
        validation_context = await self._optional_validation_context(context)
        compatibility = (
            evaluate_compatibility(item, validation_context)
            if validation_context is not None
            else None
        )
        platform_compatible = (
            compatibility.platform_compatible if compatibility is not None else None
        )
        return _item_resource(
            item,
            installation,
            update_available=_is_update(item, installation),
            platform_compatible=platform_compatible,
            compatibility_decision=compatibility,
            route_available=self.distribution.route_available(item),
            owner_extension=await self._owner_extension(item, installation),
        )

    async def _list_compatibility_context(
        self,
        context: RequestContext,
        plan: _RegistryQueryPlan,
    ) -> tuple[ValidationContext | None, str | None]:
        version = plan.compatibility_platform_version
        if plan.compatible is None:
            return None, version
        if self.validation_context_resolver is None:
            if version is None:
                await self._current_platform_version(context)
            return None, version

        validation = await self.validation_context_resolver.resolve(context)
        if version is not None:
            validation = replace(validation, platform_version=version)
        else:
            version = validation.platform_version
        return validation, version

    def _search_items(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        try:
            return self.distribution.search(query)
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

    def _list_item_resource(
        self,
        item: RegistryItem,
        plan: _RegistryQueryPlan,
        compatibility_context: ValidationContext | None,
        compatibility_version: str | None,
    ) -> dict[str, JsonValue] | None:
        if plan.sources and item.source_registry not in plan.sources:
            return None
        installation = self.distribution.installed(item.item_id)
        has_update = _is_update(item, installation)
        compatibility = (
            evaluate_compatibility(item, compatibility_context)
            if compatibility_context is not None
            else None
        )
        is_compatible = (
            compatibility.compatible
            if compatibility is not None
            else self._platform_compatible(item, compatibility_version)
        )
        if plan.installed is not None and (installation is not None) is not plan.installed:
            return None
        if plan.update_available is not None and has_update is not plan.update_available:
            return None
        if plan.deprecated is not None and item.deprecated is not plan.deprecated:
            return None
        if plan.yanked is not None and item.yanked is not plan.yanked:
            return None
        if plan.compatible is not None and is_compatible is not plan.compatible:
            return None
        return _item_resource(
            item,
            installation,
            update_available=has_update,
            platform_compatible=(
                compatibility.platform_compatible if compatibility is not None else is_compatible
            ),
            compatibility_decision=compatibility,
            route_available=self.distribution.route_available(item),
        )

    def _get_item(
        self,
        item_id: str,
        version: str | None,
        *,
        source_registry: str | None,
    ) -> RegistryItem:
        try:
            return self.distribution.get(
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

    async def _owner_extension(
        self,
        item: RegistryItem,
        installation: RegistryInstallation | None,
    ) -> dict[str, JsonValue] | None:
        if item.route not in {
            DistributionRoute.KIND_HANDLER,
            DistributionRoute.PLUGIN,
        }:
            return None
        try:
            requirements = self.distribution.inspect_requirements(item)
            details = self.distribution.describe(item)
            status_item = self._installed_status_item(item, installation)
            status = (
                await self.distribution.status(status_item) if status_item is not None else None
            )
        except ContractError:
            raise
        # error-boundary: allow-broad-catch=translation reviewed owner detail translation
        except Exception as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "marketplace owner detail provider failed",
                details={"marketplace_reason": "owner_failure", "kind": item.kind},
            ) from exc
        descriptor = self.distribution.kind_descriptor(item)
        supported_operations = (
            [
                operation
                for operation, supported in (
                    ("install", descriptor.supports_install),
                    ("update", descriptor.supports_update),
                    ("uninstall", descriptor.supports_uninstall),
                )
                if supported
            ]
            if descriptor is not None
            else []
        )
        return {
            "handler_available": self.distribution.has_kind_handler(item),
            "supported_operations": _json_strings(supported_operations),
            "requirements": json_value(requirements) if requirements is not None else None,
            "details": json_value(details) if details is not None else None,
            "status": json_value(status) if status is not None else None,
            "status_version": status_item.version if status_item is not None else None,
        }

    def _installed_status_item(
        self,
        item: RegistryItem,
        installation: RegistryInstallation | None,
    ) -> RegistryItem | None:
        if installation is None:
            return None
        current = installation.current
        if current.version == item.version and current.source_registry == item.source_registry:
            return item
        try:
            return self.distribution.get(
                item.item_id,
                current.version,
                source_registry=current.source_registry,
            )
        except LookupError:
            return None

    @staticmethod
    def _platform_compatible(
        item: RegistryItem,
        platform_version: str | None,
    ) -> bool | None:
        if platform_version is None:
            return None
        return item.supported_platform.contains(platform_version)

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
        installed_key = version_key(installation.current.version)
        candidate_key = version_key(preview.item.version)
        same_version_source_change = (
            candidate_key == installed_key
            and preview.item.source_registry != installation.current.source_registry
        )
        if candidate_key < installed_key or (
            candidate_key == installed_key and not same_version_source_change
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                (
                    "marketplace update must use a newer version or an explicitly selected "
                    "different Marketplace source"
                ),
                details={
                    "marketplace_reason": "not_newer",
                    "installed_version": installation.current.version,
                    "installed_source_registry": installation.current.source_registry,
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
        preview = self._resolve_uninstall_preview(resource_ref, payload)
        self._require_uninstall_owner(preview)
        _require_marketplace_uninstall(preview)
        await self._uninstall_owner(preview)
        return {
            "id": resource_ref,
            "type": "marketplace-mutation",
            "action": "uninstall",
            "status": "applied",
            "route": preview.item.route.value,
            "decision": _decision_resource(preview.decision),
            "installation": None,
        }

    def _resolve_uninstall_preview(
        self,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> DistributionUninstallPreview:
        if payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "marketplace.uninstall accepts no payload fields",
            )
        if self.distribution.installed(resource_ref) is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"marketplace item {resource_ref!r} is not installed",
                details={"marketplace_reason": "not_installed"},
            )
        try:
            return self.distribution.preview_uninstall(resource_ref)
        except RegistrySourceConflictError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                str(exc),
                details={"marketplace_reason": "source_ambiguous"},
            ) from exc
        except LookupError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                "installed Marketplace metadata is unavailable from its recorded source",
                details={"marketplace_reason": "installed_metadata_unavailable"},
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

    def _require_uninstall_owner(
        self,
        preview: DistributionUninstallPreview,
    ) -> None:
        if preview.item.route not in {
            DistributionRoute.KIND_HANDLER,
            DistributionRoute.PLUGIN,
        }:
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

    async def _uninstall_owner(
        self,
        preview: DistributionUninstallPreview,
    ) -> None:
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
            self._raise_uninstall_runtime_error(preview, exc)
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

    @staticmethod
    def _raise_uninstall_runtime_error(
        preview: DistributionUninstallPreview,
        exc: RuntimeError,
    ) -> None:
        drift_errors = {
            "registry provider changed after preview",
            "registry metadata changed after preview",
            "installed registry state changed after preview",
            "registry decision state changed after preview",
        }
        if str(exc) in drift_errors:
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
    if preview.route is DistributionRoute.KIND_HANDLER and not route_available:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "marketplace owner handler is unavailable",
            details={
                "marketplace_reason": "missing_handler",
                "kind": preview.item.kind,
                "route": preview.route.value,
            },
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
