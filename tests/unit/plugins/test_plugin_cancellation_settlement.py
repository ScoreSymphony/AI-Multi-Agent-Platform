from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.plugins import (
    ExtensionRegistration,
    ExtensionType,
    PluginExtensionSpec,
    PluginHealthReport,
    PluginRegistry,
    PluginState,
    reference_manifest,
)
from ai_multi_agent_platform.plugins.models import PluginHealth
from ai_multi_agent_platform.plugins.runtime import PluginContext


class _RecordingBinder:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unregister_started = asyncio.Event()
        self.release_unregister = asyncio.Event()

    async def register(self, registration: ExtensionRegistration) -> None:
        self.registered.append(registration.spec.extension_id)

    async def unregister(self, registration: ExtensionRegistration) -> None:
        self.unregister_started.set()
        await self.release_unregister.wait()
        self.registered.remove(registration.spec.extension_id)


class _BlockingHealthRuntime:
    def __init__(self, extensions: tuple[PluginExtensionSpec, ...]) -> None:
        self.extensions = extensions
        self.health_started = asyncio.Event()
        self.shutdown_called = False

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        del context
        return tuple(
            ExtensionRegistration(spec=extension, instance=object())
            for extension in self.extensions
        )

    async def health(self) -> PluginHealthReport:
        self.health_started.set()
        await asyncio.Event().wait()
        return PluginHealthReport(PluginHealth.HEALTHY)

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_enable_settles_rollback_before_repeated_task_cancellation_propagates() -> None:
    async def scenario() -> None:
        binder = _RecordingBinder()
        registry = PluginRegistry(
            platform_version="0.0.1",
            supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
            binders={ExtensionType.CAPABILITY_PROVIDER: binder},
        )
        manifest = replace(reference_manifest(), requested_permissions=frozenset())
        runtime = _BlockingHealthRuntime(manifest.extensions)
        registry.install(manifest)

        enabling = asyncio.create_task(registry.enable(manifest.plugin_id, runtime))
        await runtime.health_started.wait()
        assert binder.registered == [manifest.extensions[0].extension_id]

        enabling.cancel()
        await binder.unregister_started.wait()
        enabling.cancel()
        binder.release_unregister.set()

        with pytest.raises(asyncio.CancelledError):
            await enabling

        snapshot = registry.get(manifest.plugin_id)
        assert snapshot.state is PluginState.FAILED
        assert snapshot.health is PluginHealth.UNAVAILABLE
        assert binder.registered == []
        assert runtime.shutdown_called is True
        assert registry.extension_owner(manifest.extensions[0].extension_id) is None

    asyncio.run(scenario())
