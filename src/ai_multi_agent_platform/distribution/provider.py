"""Replaceable optional registry-provider boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .items import RegistryItem, RegistryQuery


class RegistryUnavailableError(RuntimeError):
    """A configured registry cannot currently be reached."""


class RegistryItemNotFoundError(LookupError):
    """The requested registry item/version does not exist."""


@runtime_checkable
class RegistryProvider(Protocol):
    """Distribution provider; core platform operation never depends on its presence."""

    @property
    def provider_id(self) -> str: ...

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]: ...

    def get(self, item_id: str, version: str | None = None) -> RegistryItem: ...

    def fetch_artifact(self, item_id: str, version: str) -> bytes: ...
