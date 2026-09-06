"""Guarded registry discovery, preview and explicit activation workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .items import RegistryItem, RegistryQuery
from .models import DistributionRoute
from .provider import RegistryProvider
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
    ) -> None:
        self._provider = provider
        self._router = router

    @property
    def enabled(self) -> bool:
        return self._provider is not None

    @property
    def activation_enabled(self) -> bool:
        return self._provider is not None and self._router is not None

    def search(self, query: RegistryQuery | None = None) -> tuple[RegistryItem, ...]:
        """Discover registry metadata without exposing a concrete provider northbound."""

        return self._require_provider().search(query or RegistryQuery())

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        """Read exact registry metadata through the provider-neutral domain boundary."""

        return self._require_provider().get(item_id, version)

    def preview(
        self,
        item_id: str,
        version: str,
        context: ValidationContext,
    ) -> DistributionPreview:
        provider = self._require_provider()
        item = provider.get(item_id, version)
        artifact = provider.fetch_artifact(item_id, version)
        findings = validate_item(item, artifact, context)
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
        findings = validate_item(current, artifact, context)
        if has_errors(findings):
            raise ValueError("registry item no longer passes activation validation")
        if self._router is None:
            raise RuntimeError("distribution activation router is not configured")
        if current.route is DistributionRoute.PLUGIN:
            return await self._router.install_plugin(current, artifact)
        if current.route is DistributionRoute.PORTABLE_IMPORT:
            return await self._router.import_portable(current, artifact)
        raise RuntimeError("manual registry assets cannot be activated automatically")

    def _require_provider(self) -> RegistryProvider:
        if self._provider is None:
            raise RuntimeError("registry is disabled")
        return self._provider
