"""Guarded registry discovery, preview and explicit activation workflow."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .handlers import MarketplaceKindHandler, MarketplaceKindHandlerRegistry
from .items import RegistryItem, RegistryQuery
from .kinds import builtin_marketplace_kind
from .models import DistributionRoute
from .provider import RegistryProvider
from .signatures import RegistrySignatureVerifier
from .state import RegistryInstallation, RegistryInstallationStore
from .technical_catalog import derive_technical_metadata
from .validation import (
    FindingSeverity,
    ValidationContext,
    ValidationFinding,
    has_errors,
    validate_item,
)


class DistributionRouter(Protocol):
    """Hands validated legacy distribution routes to existing owner domains (#20/#78/#79)."""

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object: ...

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class DistributionPreview:
    provider_id: str
    item: RegistryItem
    route: DistributionRoute
    findings: tuple[ValidationFinding, ...]
    activation_allowed: bool


class DistributionService:
    def __init__(
        self,
        provider: RegistryProvider | None,
        router: DistributionRouter | None = None,
        *,
        installations: RegistryInstallationStore | None = None,
        signature_verifier: RegistrySignatureVerifier | None = None,
        kind_handlers: MarketplaceKindHandlerRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._router = router
        self._installations = installations
        self._signature_verifier = signature_verifier
        self._kind_handlers = kind_handlers or MarketplaceKindHandlerRegistry()

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    @property
    def activation_enabled(self) -> bool:
        return self._provider is not None and (
            self._router is not None or bool(self._kind_handlers.kinds())
        )

    @property
    def installation_state_enabled(self) -> bool:
        return self._installations is not None

    def search(self, query: RegistryQuery | None = None) -> tuple[RegistryItem, ...]:
        """Discover validated registry metadata without exposing a concrete provider northbound."""

        items = self._require_provider().search(query or RegistryQuery())
        return tuple(_validate_provider_metadata(item) for item in items)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        """Read exact validated registry metadata through the provider-neutral domain boundary."""

        item = self._require_provider().get(item_id, version)
        return _validate_provider_metadata(item)

    def installed(self, item_id: str) -> RegistryInstallation | None:
        if self._installations is None:
            return None
        return self._installations.get(item_id)

    def installed_items(self) -> tuple[RegistryInstallation, ...]:
        if self._installations is None:
            return ()
        return self._installations.list()

    def available_updates(self, item_id: str) -> tuple[RegistryItem, ...]:
        installation = self.installed(item_id)
        if installation is None:
            return ()
        installed = installation.as_installed()
        candidates = self.search(RegistryQuery(update_for_item_id=item_id))
        return tuple(candidate for candidate in candidates if installed.has_update(candidate))

    def pin(self, item_id: str, version: str) -> RegistryInstallation:
        return self._require_installations().pin(item_id, version)

    def unpin(self, item_id: str) -> RegistryInstallation:
        return self._require_installations().unpin(item_id)

    def inspect_requirements(
        self,
        item_id: str,
        version: str | None = None,
    ) -> Mapping[str, object]:
        item = self.get(item_id, version)
        handler = self._require_kind_handler(item, operation="status")
        return handler.inspect_requirements(item)

    def describe(
        self,
        item_id: str,
        version: str | None = None,
    ) -> Mapping[str, object]:
        installation = self.installed(item_id)
        resolved_version = (
            installation.current.version
            if version is None and installation is not None
            else version
        )
        item = self.get(item_id, resolved_version)
        handler = self._require_kind_handler(item, operation="status")
        return handler.describe(item)

    async def status(self, item_id: str) -> object:
        installation = self.installed(item_id)
        if installation is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Marketplace item is not installed: {item_id}",
            )
        item = self.get(item_id, installation.current.version)
        handler = self._require_kind_handler(item, operation="status")
        return await handler.status(item)

    async def uninstall(self, item_id: str, *, authorized: bool) -> object:
        if not authorized:
            raise PermissionError("registry uninstall requires explicit authorization")
        installations = self._require_installations()
        installation = installations.get(item_id)
        if installation is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Marketplace item is not installed: {item_id}",
            )
        item = self.get(item_id, installation.current.version)
        handler = self._require_kind_handler(item, operation="uninstall")
        result = await handler.uninstall(item)
        installations.remove(item_id)
        return result

    def preview(
        self,
        item_id: str,
        version: str,
        context: ValidationContext,
    ) -> DistributionPreview:
        provider = self._require_provider()
        item = _validate_provider_metadata(provider.get(item_id, version))
        artifact = provider.fetch_artifact(item_id, version)
        resolved_context = self._resolved_context(item, artifact, context)
        findings = validate_item(item, artifact, resolved_context)
        handler_available = (
            item.route is not DistributionRoute.KIND_HANDLER
            or self._kind_handlers.get(item.item_type) is not None
        )
        operation = self._activation_operation(item)
        operation_supported = (
            item.route is not DistributionRoute.KIND_HANDLER
            or operation == "status"
            or _kind_supports(item, operation)
        )
        if (
            item.route is DistributionRoute.KIND_HANDLER
            and handler_available
            and not operation_supported
        ):
            findings = (
                *findings,
                ValidationFinding(
                    "unsupported_operation",
                    FindingSeverity.ERROR,
                    f"{item.kind} owner does not support Marketplace {operation}",
                ),
            )
        return DistributionPreview(
            provider_id=provider.provider_id,
            item=item,
            route=item.route,
            findings=findings,
            activation_allowed=(
                item.route is not DistributionRoute.MANUAL
                and handler_available
                and operation_supported
                and not has_errors(findings)
            ),
        )

    async def activate(
        self,
        preview: DistributionPreview,
        context: ValidationContext,
        *,
        authorized: bool,
    ) -> object:
        if not authorized:
            raise PermissionError("registry activation requires explicit authorization")
        provider = self._require_provider()
        if provider.provider_id != preview.provider_id:
            raise RuntimeError("registry provider changed after preview")
        current = _validate_provider_metadata(
            provider.get(preview.item.item_id, preview.item.version)
        )
        if current != preview.item:
            raise RuntimeError("registry metadata changed after preview")
        artifact = provider.fetch_artifact(current.item_id, current.version)
        resolved_context = self._resolved_context(current, artifact, context)
        findings = validate_item(current, artifact, resolved_context)
        if has_errors(findings):
            error_findings: list[JsonValue] = [
                {"code": finding.code, "message": finding.message}
                for finding in findings
                if finding.severity is FindingSeverity.ERROR
            ]
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "registry item no longer passes activation validation",
                details={"findings": error_findings},
            )
        if current.route is DistributionRoute.KIND_HANDLER:
            operation = self._activation_operation(current)
            handler = self._require_kind_handler(current, operation=operation)
            if operation == "install":
                result = await handler.install(current, artifact)
            elif operation == "update":
                result = await handler.update(current, artifact)
            else:
                result = await handler.status(current)
        elif current.route is DistributionRoute.PLUGIN:
            result = await self._require_router().install_plugin(current, artifact)
        elif current.route is DistributionRoute.PORTABLE_IMPORT:
            result = await self._require_router().import_portable(current, artifact)
        else:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "manual registry assets cannot be activated automatically",
            )
        if self._installations is not None:
            self._installations.record(
                current,
                provider_id=provider.provider_id,
                artifact_sha256=hashlib.sha256(artifact).hexdigest(),
            )
        return result

    def _activation_operation(self, item: RegistryItem) -> str:
        installation = self.installed(item.item_id)
        if installation is None:
            return "install"
        if installation.current.version == item.version:
            return "status"
        return "update"

    def _require_kind_handler(
        self,
        item: RegistryItem,
        *,
        operation: str,
    ) -> MarketplaceKindHandler:
        if operation != "status" and not _kind_supports(item, operation):
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"{item.kind} owner does not support Marketplace {operation}",
            )
        return self._kind_handlers.require(item.item_type)

    def _resolved_context(
        self,
        item: RegistryItem,
        artifact: bytes,
        context: ValidationContext,
    ) -> ValidationContext:
        resolved = context
        if self._installations is not None:
            resolved = replace(
                resolved,
                installed_items=tuple(
                    record.as_installed() for record in self._installations.list()
                ),
            )
        if item.integrity.signature is not None:
            signature_valid = (
                self._signature_verifier.verify(item, artifact)
                if self._signature_verifier is not None
                else None
            )
            resolved = replace(resolved, signature_valid=signature_valid)
        return resolved

    def _require_provider(self) -> RegistryProvider:
        if self._provider is None:
            raise RuntimeError("registry is disabled")
        return self._provider

    def _require_router(self) -> DistributionRouter:
        if self._router is None:
            raise RuntimeError("distribution activation router is not configured")
        return self._router

    def _require_installations(self) -> RegistryInstallationStore:
        if self._installations is None:
            raise RuntimeError("registry installation persistence is not configured")
        return self._installations


def _validate_provider_metadata(item: RegistryItem) -> RegistryItem:
    """Fail closed on malformed technical metadata from any replaceable provider."""

    derive_technical_metadata(item)
    return item


def _kind_supports(item: RegistryItem, operation: str) -> bool:
    descriptor = builtin_marketplace_kind(item.item_type)
    if descriptor is None:
        return True
    if operation == "install":
        return descriptor.supports_install
    if operation == "update":
        return descriptor.supports_update
    if operation == "uninstall":
        return descriptor.supports_uninstall
    if operation == "status":
        return True
    raise ValueError(f"unknown Marketplace operation: {operation}")
