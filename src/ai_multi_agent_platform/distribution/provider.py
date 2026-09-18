"""Replaceable optional registry-provider boundary."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Protocol, runtime_checkable

from .items import RegistryItem, RegistryQuery
from .models import version_key


class RegistryUnavailableError(RuntimeError):
    """A configured registry cannot currently be reached."""


class RegistryItemNotFoundError(LookupError):
    """The requested registry item/version does not exist."""


class RegistrySourceConflictError(LookupError):
    """More than one configured source can satisfy an unqualified Registry identity."""


@runtime_checkable
class RegistryProvider(Protocol):
    """Distribution provider; core platform operation never depends on its presence."""

    @property
    def provider_id(self) -> str: ...

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]: ...

    def get(self, item_id: str, version: str | None = None) -> RegistryItem: ...

    def fetch_artifact(self, item_id: str, version: str) -> bytes: ...


@runtime_checkable
class SourcedRegistryProvider(RegistryProvider, Protocol):
    """Optional source-aware extension used by unified multi-catalog providers."""

    def get_from_source(
        self,
        source_registry: str,
        item_id: str,
        version: str | None = None,
    ) -> RegistryItem: ...

    def fetch_artifact_from_source(
        self,
        source_registry: str,
        item_id: str,
        version: str,
    ) -> bytes: ...


class MultiRegistryProvider:
    """Present multiple catalogs without silently collapsing source identity."""

    def __init__(
        self,
        providers: Iterable[RegistryProvider],
        *,
        provider_id: str = "multi",
    ) -> None:
        resolved = tuple(providers)
        if not resolved:
            raise ValueError("multi registry provider requires at least one source")
        source_ids = [provider.provider_id for provider in resolved]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("multi registry provider requires unique source provider IDs")
        if not provider_id.strip():
            raise ValueError("registry provider_id must be non-blank")
        self._providers = resolved
        self._by_id = {provider.provider_id: provider for provider in resolved}
        self._provider_id = provider_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        matches = [
            replace(item, source_registry=provider.provider_id)
            for provider in self._providers
            for item in provider.search(query)
        ]
        matches.sort(
            key=lambda item: (
                item.item_id,
                tuple(-part for part in version_key(item.version)),
                item.source_registry or "",
            )
        )
        return tuple(matches)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        matches: list[RegistryItem] = []
        for provider in self._providers:
            try:
                item = provider.get(item_id, version)
            except RegistryItemNotFoundError:
                continue
            matches.append(replace(item, source_registry=provider.provider_id))
        if not matches:
            raise RegistryItemNotFoundError(f"registry item {item_id!r} not found")
        if len(matches) != 1:
            sources = ", ".join(sorted(item.source_registry or "" for item in matches))
            raise RegistrySourceConflictError(
                f"registry item {item_id!r} is available from multiple sources: {sources}"
            )
        return matches[0]

    def get_from_source(
        self,
        source_registry: str,
        item_id: str,
        version: str | None = None,
    ) -> RegistryItem:
        provider = self._require_source(source_registry)
        item = provider.get(item_id, version)
        return replace(item, source_registry=source_registry)

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        item = self.get(item_id, version)
        if item.source_registry is None:
            raise RuntimeError("multi registry item lost source identity")
        return self.fetch_artifact_from_source(item.source_registry, item_id, version)

    def fetch_artifact_from_source(
        self,
        source_registry: str,
        item_id: str,
        version: str,
    ) -> bytes:
        return self._require_source(source_registry).fetch_artifact(item_id, version)

    def _require_source(self, source_registry: str) -> RegistryProvider:
        try:
            return self._by_id[source_registry]
        except KeyError as exc:
            raise RegistryItemNotFoundError(
                f"registry source {source_registry!r} is not configured"
            ) from exc
