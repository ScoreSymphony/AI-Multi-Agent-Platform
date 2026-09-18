"""Guarded registry discovery, preview and explicit activation workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Protocol

from .decision import build_marketplace_decision, uninstall_decision
from .decision_types import DependencyResolution, DependencyStatus, MarketplaceDecision
from .dependency_graph import dependency_findings
from .handlers import MarketplaceKindHandlerRegistry
from .items import RegistryItem, RegistryQuery
from .models import DistributionRoute
from .provider import (
    RegistryItemNotFoundError,
    RegistryProvider,
    SourcedRegistryProvider,
)
from .signatures import RegistrySignatureVerifier
from .state import RegistryInstallation, RegistryInstallationStore
from .technical_catalog import derive_technical_metadata
from .validation import (
    FindingCategory,
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
    artifact_sha256: str
    decision: MarketplaceDecision

    @property
    def approval_required(self) -> bool:
        return self.decision.approval.required


@dataclass(frozen=True, slots=True)
class DistributionUninstallPreview:
    provider_id: str
    item: RegistryItem
    installation: RegistryInstallation
    findings: tuple[ValidationFinding, ...]
    activation_allowed: bool
    decision: MarketplaceDecision


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

    def has_kind_handler(self, item: RegistryItem) -> bool:
        return self._kind_handlers.get(item.item_type) is not None

    def route_available(self, item: RegistryItem) -> bool:
        if item.route is DistributionRoute.KIND_HANDLER:
            return self.has_kind_handler(item)
        if item.route in {DistributionRoute.PLUGIN, DistributionRoute.PORTABLE_IMPORT}:
            return self._router is not None
        return False

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object] | None:
        handler = self._kind_handlers.get(item.item_type)
        if handler is None:
            return None
        return dict(handler.inspect_requirements(item))

    def describe(self, item: RegistryItem) -> dict[str, object] | None:
        handler = self._kind_handlers.get(item.item_type)
        if handler is None:
            return None
        return dict(handler.describe(item))

    def status(self, item: RegistryItem) -> object | None:
        handler = self._kind_handlers.get(item.item_type)
        if handler is None:
            return None
        return handler.status(item)

    def search(self, query: RegistryQuery | None = None) -> tuple[RegistryItem, ...]:
        """Discover validated registry metadata without exposing a concrete provider northbound."""

        provider = self._require_provider()
        items = provider.search(query or RegistryQuery())
        return tuple(
            _validate_provider_metadata(self._with_source_identity(item, provider))
            for item in items
        )

    def get(
        self,
        item_id: str,
        version: str | None = None,
        *,
        source_registry: str | None = None,
    ) -> RegistryItem:
        """Read exact validated metadata, optionally qualified by Marketplace source."""

        provider = self._require_provider()
        item = self._get_from_provider(
            provider,
            item_id,
            version,
            source_registry=source_registry,
        )
        return _validate_provider_metadata(self._with_source_identity(item, provider))

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
        candidates = self.search(
            RegistryQuery(
                update_for_item_id=item_id,
                include_deprecated=True,
                include_yanked=True,
            )
        )
        return tuple(candidate for candidate in candidates if installed.has_update(candidate))

    def pin(self, item_id: str, version: str) -> RegistryInstallation:
        return self._require_installations().pin(item_id, version)

    def unpin(self, item_id: str) -> RegistryInstallation:
        return self._require_installations().unpin(item_id)

    def preview(
        self,
        item_id: str,
        version: str,
        context: ValidationContext,
        *,
        source_registry: str | None = None,
    ) -> DistributionPreview:
        provider = self._require_provider()
        installation = self.installed(item_id)
        preferred_source = source_registry
        if preferred_source is None and installation is not None:
            preferred_source = installation.current.source_registry
        item = self.get(
            item_id,
            version,
            source_registry=preferred_source,
        )
        artifact = self._fetch_artifact(provider, item)
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        resolved_context = self._resolved_context(item, artifact, context)
        base_findings = validate_item(item, artifact, resolved_context)
        catalog = self.search(RegistryQuery(include_deprecated=True, include_yanked=True))
        decision, findings = build_marketplace_decision(
            item,
            artifact_sha256=artifact_sha256,
            context=resolved_context,
            catalog=catalog,
            installation=installation,
            validation_findings=base_findings,
        )
        handler_available = (
            item.route is not DistributionRoute.KIND_HANDLER
            or self._kind_handlers.get(item.item_type) is not None
        )
        if not handler_available:
            findings = (
                *findings,
                ValidationFinding(
                    "handler_unavailable",
                    FindingSeverity.ERROR,
                    f"no owner-domain Marketplace handler is registered for kind {item.kind!r}",
                    FindingCategory.COMPATIBILITY,
                    item.kind,
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
                and not has_errors(findings)
            ),
            artifact_sha256=artifact_sha256,
            decision=decision,
        )

    def preview_uninstall(
        self,
        item_id: str,
    ) -> DistributionUninstallPreview:
        installation = self.installed(item_id)
        if installation is None:
            raise LookupError(f"registry item {item_id!r} is not installed")
        provider = self._require_provider()
        item = self.get(
            installation.current.item_id,
            installation.current.version,
            source_registry=installation.current.source_registry,
        )
        reverse_dependencies: list[DependencyResolution] = []
        for dependent in self.installed_items():
            if dependent.current.item_id == item_id:
                continue
            dependencies = dependent.current.dependencies
            if dependencies is None:
                try:
                    dependent_item = self.get(
                        dependent.current.item_id,
                        dependent.current.version,
                        source_registry=dependent.current.source_registry,
                    )
                except LookupError:
                    reverse_dependencies.append(
                        DependencyResolution(
                            required_by=dependent.current.item_id,
                            item_id=item_id,
                            item_kind=installation.current.as_installed().kind,
                            optional=False,
                            minimum_version=None,
                            maximum_version=None,
                            status=DependencyStatus.UNKNOWN_INSTALLED_DEPENDENT,
                            installed_version=installation.current.version,
                            path=(dependent.current.item_id, item_id),
                        )
                    )
                    continue
                dependencies = dependent_item.dependencies
            for dependency in dependencies:
                if dependency.optional or dependency.item_id != item_id:
                    continue
                if not dependency.version_range.contains(installation.current.version):
                    continue
                reverse_dependencies.append(
                    DependencyResolution(
                        required_by=dependent.current.item_id,
                        item_id=item_id,
                        item_kind=dependency.kind_value,
                        optional=False,
                        minimum_version=dependency.version_range.minimum,
                        maximum_version=dependency.version_range.maximum,
                        status=DependencyStatus.REQUIRED_BY_INSTALLED,
                        installed_version=installation.current.version,
                        path=(dependent.current.item_id, item_id),
                    )
                )
        resolved_dependencies = tuple(reverse_dependencies)
        findings = dependency_findings(resolved_dependencies)
        decision = uninstall_decision(
            installation,
            dependencies=resolved_dependencies,
        )
        return DistributionUninstallPreview(
            provider_id=provider.provider_id,
            item=item,
            installation=installation,
            findings=findings,
            activation_allowed=not has_errors(findings),
            decision=decision,
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

        current_preview = self.preview(
            preview.item.item_id,
            preview.item.version,
            context,
            source_registry=preview.item.source_registry,
        )
        if current_preview.item != preview.item:
            raise RuntimeError("registry metadata changed after preview")
        if current_preview.artifact_sha256 != preview.artifact_sha256:
            raise RuntimeError("registry artifact changed after preview")
        if current_preview.decision != preview.decision:
            raise RuntimeError("registry decision state changed after preview")
        if not current_preview.activation_allowed:
            raise ValueError("registry item no longer passes activation validation")

        current = current_preview.item
        artifact = self._fetch_artifact(provider, current)
        if hashlib.sha256(artifact).hexdigest() != current_preview.artifact_sha256:
            raise RuntimeError("registry artifact changed immediately before activation")
        if current.route is DistributionRoute.KIND_HANDLER:
            handler = self._kind_handlers.require(current.item_type)
            if self.installed(current.item_id) is None:
                result = await handler.install(current, artifact)
            else:
                result = await handler.update(current, artifact)
        elif current.route is DistributionRoute.PLUGIN:
            result = await self._require_router().install_plugin(current, artifact)
        elif current.route is DistributionRoute.PORTABLE_IMPORT:
            result = await self._require_router().import_portable(current, artifact)
        else:
            raise RuntimeError("manual registry assets cannot be activated automatically")
        if self._installations is not None:
            self._installations.record(
                current,
                provider_id=current.source_registry or provider.provider_id,
                artifact_sha256=current_preview.artifact_sha256,
            )
        return result

    async def uninstall(
        self,
        preview: DistributionUninstallPreview,
        *,
        authorized: bool,
    ) -> object:
        if not authorized:
            raise PermissionError("marketplace uninstall requires explicit authorization")
        provider = self._require_provider()
        if provider.provider_id != preview.provider_id:
            raise RuntimeError("registry provider changed after preview")

        current_preview = self.preview_uninstall(preview.installation.current.item_id)
        if current_preview.item != preview.item:
            raise RuntimeError("registry metadata changed after preview")
        if current_preview.installation != preview.installation:
            raise RuntimeError("installed registry state changed after preview")
        if current_preview.decision != preview.decision:
            raise RuntimeError("registry decision state changed after preview")
        if not current_preview.activation_allowed:
            raise ValueError("registry item no longer passes uninstall validation")

        current = current_preview.item
        if current.route is not DistributionRoute.KIND_HANDLER:
            raise RuntimeError(
                "marketplace uninstall is not supported for distribution route "
                f"{current.route.value!r}"
            )
        handler = self._kind_handlers.get(current.item_type)
        if handler is None:
            raise KeyError(f"marketplace handler for kind {current.kind!r} is not registered")
        result = await handler.uninstall(current)
        self._require_installations().remove(current.item_id)
        return result

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

    def _get_from_provider(
        self,
        provider: RegistryProvider,
        item_id: str,
        version: str | None,
        *,
        source_registry: str | None,
    ) -> RegistryItem:
        if source_registry is None:
            return provider.get(item_id, version)
        if isinstance(provider, SourcedRegistryProvider):
            return provider.get_from_source(source_registry, item_id, version)
        if source_registry != provider.provider_id:
            raise RegistryItemNotFoundError(
                f"registry source {source_registry!r} is not configured"
            )
        return provider.get(item_id, version)

    def _fetch_artifact(
        self,
        provider: RegistryProvider,
        item: RegistryItem,
    ) -> bytes:
        if item.source_registry is not None and isinstance(provider, SourcedRegistryProvider):
            return provider.fetch_artifact_from_source(
                item.source_registry,
                item.item_id,
                item.version,
            )
        return provider.fetch_artifact(item.item_id, item.version)

    @staticmethod
    def _with_source_identity(
        item: RegistryItem,
        provider: RegistryProvider,
    ) -> RegistryItem:
        if item.source_registry is None:
            return replace(item, source_registry=provider.provider_id)
        if isinstance(provider, SourcedRegistryProvider):
            return item
        if item.source_registry != provider.provider_id:
            raise ValueError("registry provider returned conflicting source_registry identity")
        return item

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
