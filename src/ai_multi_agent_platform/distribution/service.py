"""Guarded registry discovery, preview and explicit activation workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from .items import RegistryItem, RegistryQuery
from .models import DistributionRoute
from .provider import RegistryProvider
from .signatures import RegistrySignatureVerifier
from .state import RegistryInstallation, RegistryInstallationStore
from .validation import ValidationContext, ValidationFinding, has_errors, validate_item


class DistributionRouter(Protocol):
    """Hands validated content to the existing owner domains (#20/#78/#79)."""

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
    ) -> None:
        self._provider = provider
        self._router = router
        self._installations = installations
        self._signature_verifier = signature_verifier

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    @property
    def activation_enabled(self) -> bool:
        return self._provider is not None and self._router is not None

    @property
    def installation_state_enabled(self) -> bool:
        return self._installations is not None

    def search(self, query: RegistryQuery | None = None) -> tuple[RegistryItem, ...]:
        """Discover registry metadata without exposing a concrete provider northbound."""

        return self._require_provider().search(query or RegistryQuery())

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        """Read exact registry metadata through the provider-neutral domain boundary."""

        return self._require_provider().get(item_id, version)

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
        return tuple(candidate for candidate in candidates if installed.accepts_update(candidate))

    def pin(self, item_id: str, version: str) -> RegistryInstallation:
        return self._require_installations().pin(item_id, version)

    def unpin(self, item_id: str) -> RegistryInstallation:
        return self._require_installations().unpin(item_id)

    def preview(
        self,
        item_id: str,
        version: str,
        context: ValidationContext,
    ) -> DistributionPreview:
        provider = self._require_provider()
        item = provider.get(item_id, version)
        artifact = provider.fetch_artifact(item_id, version)
        resolved_context = self._resolved_context(item, artifact, context)
        findings = validate_item(item, artifact, resolved_context)
        return DistributionPreview(
            provider_id=provider.provider_id,
            item=item,
            route=item.route,
            findings=findings,
            activation_allowed=not has_errors(findings),
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
        current = provider.get(preview.item.item_id, preview.item.version)
        if current != preview.item:
            raise RuntimeError("registry metadata changed after preview")
        artifact = provider.fetch_artifact(current.item_id, current.version)
        resolved_context = self._resolved_context(current, artifact, context)
        findings = validate_item(current, artifact, resolved_context)
        if has_errors(findings):
            raise ValueError("registry item no longer passes activation validation")
        if self._router is None:
            raise RuntimeError("distribution activation router is not configured")
        if current.route is DistributionRoute.PLUGIN:
            result = await self._router.install_plugin(current, artifact)
        elif current.route is DistributionRoute.PORTABLE_IMPORT:
            result = await self._router.import_portable(current, artifact)
        else:
            raise RuntimeError("manual registry assets cannot be activated automatically")
        if self._installations is not None:
            self._installations.record(current, provider_id=provider.provider_id)
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

    def _require_provider(self) -> RegistryProvider:
        if self._provider is None:
            raise RuntimeError("registry is disabled")
        return self._provider

    def _require_installations(self) -> RegistryInstallationStore:
        if self._installations is None:
            raise RuntimeError("registry installation persistence is not configured")
        return self._installations
