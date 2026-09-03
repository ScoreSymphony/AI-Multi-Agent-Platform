"""Explicit plugin discovery without importing arbitrary manifest entrypoints."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import PluginManifest, PluginSnapshot
from .registry import PluginRegistry
from .runtime import PluginRuntime

PluginRuntimeFactory = Callable[[], PluginRuntime]


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    manifest: PluginManifest
    runtime_factory: PluginRuntimeFactory
    install_source: str

    def __post_init__(self) -> None:
        if not self.install_source.strip():
            raise ValueError("plugin install_source must be non-blank")


class PluginSource(Protocol):
    def discover(self) -> tuple[DiscoveredPlugin, ...]: ...


class StaticPluginSource:
    """Trusted composition source for bundled or explicitly supplied plugin candidates."""

    def __init__(self, *candidates: DiscoveredPlugin) -> None:
        self._candidates = tuple(candidates)

    def discover(self) -> tuple[DiscoveredPlugin, ...]:
        return tuple(deepcopy(candidate) for candidate in self._candidates)


class PluginCatalog:
    """Collect candidates from explicit sources and install them through PluginRegistry."""

    def __init__(self, *sources: PluginSource) -> None:
        self._sources = tuple(sources)
        self._candidates: dict[str, DiscoveredPlugin] = {}

    def refresh(self) -> tuple[DiscoveredPlugin, ...]:
        candidates: dict[str, DiscoveredPlugin] = {}
        for source in self._sources:
            for candidate in source.discover():
                plugin_id = candidate.manifest.plugin_id
                if plugin_id in candidates:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        f"plugin {plugin_id!r} was discovered from more than one source",
                    )
                candidates[plugin_id] = deepcopy(candidate)
        self._candidates = candidates
        return self.list_candidates()

    def list_candidates(self) -> tuple[DiscoveredPlugin, ...]:
        return tuple(
            deepcopy(self._candidates[plugin_id]) for plugin_id in sorted(self._candidates)
        )

    def candidate(self, plugin_id: str) -> DiscoveredPlugin:
        try:
            return deepcopy(self._candidates[plugin_id])
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"plugin {plugin_id!r} is not present in the discovery catalog",
            ) from exc

    def install(self, plugin_id: str, registry: PluginRegistry) -> PluginSnapshot:
        candidate = self.candidate(plugin_id)
        return registry.install(candidate.manifest, install_source=candidate.install_source)

    def create_runtime(self, plugin_id: str) -> PluginRuntime:
        return self.candidate(plugin_id).runtime_factory()
