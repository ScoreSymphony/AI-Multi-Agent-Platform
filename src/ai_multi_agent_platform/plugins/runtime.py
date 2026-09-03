"""Runtime contracts for optional plugin implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import PluginExtensionSpec, PluginHealthReport, PluginPermission


@dataclass(frozen=True, slots=True)
class PluginContext:
    configuration: dict[str, JsonValue]
    granted_permissions: frozenset[PluginPermission]


@dataclass(frozen=True, slots=True)
class ExtensionRegistration:
    spec: PluginExtensionSpec
    instance: object


class PluginRuntime(Protocol):
    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]: ...

    async def health(self) -> PluginHealthReport: ...

    async def shutdown(self) -> None: ...


class ExtensionBinder(Protocol):
    async def register(self, registration: ExtensionRegistration) -> None: ...

    async def unregister(self, registration: ExtensionRegistration) -> None: ...
