"""Reference local/offline registry provider for issue #81."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .items import RegistryItem, RegistryQuery
from .models import version_key
from .provider import RegistryItemNotFoundError


class LocalRegistryProvider:
    def __init__(
        self,
        items: Iterable[RegistryItem] = (),
        artifacts: Mapping[tuple[str, str], bytes] | None = None,
        *,
        provider_id: str = "local",
    ) -> None:
        if not provider_id.strip():
            raise ValueError("registry provider_id must be non-blank")
        resolved_items = tuple(items)
        identities = [(item.item_id, item.version) for item in resolved_items]
        if len(identities) != len(set(identities)):
            raise ValueError("local registry contains duplicate item/version identities")
        self._provider_id = provider_id
        self._items = resolved_items
        self._artifacts = dict(artifacts or {})

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        matches = [item for item in self._items if self._matches(item, query)]
        matches.sort(
            key=lambda item: (
                item.item_id,
                tuple(-part for part in version_key(item.version)),
            )
        )
        return tuple(matches)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        matches = [
            item
            for item in self._items
            if item.item_id == item_id and (version is None or item.version == version)
        ]
        if not matches:
            raise RegistryItemNotFoundError(f"registry item {item_id!r} not found")
        return max(matches, key=lambda item: version_key(item.version))

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        try:
            return bytes(self._artifacts[(item_id, version)])
        except KeyError as exc:
            raise RegistryItemNotFoundError(
                f"artifact for registry item {item_id!r} version {version!r} not found"
            ) from exc

    @staticmethod
    def _matches(item: RegistryItem, query: RegistryQuery) -> bool:
        if item.deprecated and not query.include_deprecated:
            return False
        if item.yanked and not query.include_yanked:
            return False
        if query.item_types and item.item_type not in query.item_types:
            return False
        if query.tags and not query.tags.issubset(item.tags):
            return False
        if query.categories and not query.categories.issubset(item.categories):
            return False
        if query.licenses and item.license not in query.licenses:
            return False
        if query.publishers and item.publisher not in query.publishers:
            return False
        if query.trust_statuses and item.trust_status not in query.trust_statuses:
            return False
        if query.required_capabilities and not query.required_capabilities.issubset(
            item.required_capabilities
        ):
            return False
        if query.platform_version and not item.supported_platform.contains(query.platform_version):
            return False
        if query.update_for_item_id and item.item_id != query.update_for_item_id:
            return False
        if query.text:
            needle = query.text.casefold()
            haystack = " ".join(
                (
                    item.item_id,
                    item.name,
                    item.description,
                    item.publisher,
                    item.license,
                    *sorted(item.tags),
                    *sorted(item.categories),
                )
            ).casefold()
            if needle not in haystack:
                return False
        return True
