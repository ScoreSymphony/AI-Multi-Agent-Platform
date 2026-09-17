"""Marketplace owner-domain handler registry for issue #1174."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from .items import RegistryItem
from .models import RegistryItemKind, registry_item_kind_value


class MarketplaceKindHandler(Protocol):
    """Owner-domain bridge for one Marketplace component kind.

    Implementations delegate to the canonical Tool, Skill, Connector, Application or future
    owner. The Marketplace remains a distribution layer and never becomes the component runtime.
    """

    @property
    def kind(self) -> RegistryItemKind: ...

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]: ...

    async def install(self, item: RegistryItem, artifact: bytes) -> object: ...

    async def update(self, item: RegistryItem, artifact: bytes) -> object: ...

    async def uninstall(self, item: RegistryItem) -> object: ...

    def status(self, item: RegistryItem) -> object: ...

    def describe(self, item: RegistryItem) -> Mapping[str, object]: ...


class MarketplaceKindHandlerRegistry:
    """Maps component kinds to owner-domain bridges without hard-coding them in Marketplace core."""

    def __init__(self, handlers: Iterable[MarketplaceKindHandler] = ()) -> None:
        self._handlers: dict[str, MarketplaceKindHandler] = {}
        for handler in handlers:
            self.register(handler)

    def register(self, handler: MarketplaceKindHandler) -> None:
        kind = registry_item_kind_value(handler.kind)
        if kind in self._handlers:
            raise ValueError(f"marketplace handler for kind {kind!r} is already registered")
        self._handlers[kind] = handler

    def get(self, kind: RegistryItemKind) -> MarketplaceKindHandler | None:
        return self._handlers.get(registry_item_kind_value(kind))

    def require(self, kind: RegistryItemKind) -> MarketplaceKindHandler:
        normalized = registry_item_kind_value(kind)
        handler = self._handlers.get(normalized)
        if handler is None:
            raise KeyError(f"marketplace handler for kind {normalized!r} is not registered")
        return handler

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
