"""Backend-neutral plugin-owned state storage and explicit migration hooks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import PluginManifest, PluginStateMigrationSpec


@dataclass(frozen=True, slots=True)
class PluginOwnedState:
    plugin_id: str
    state_version: str
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.plugin_id.strip():
            raise ValueError("plugin-owned state plugin_id must be non-blank")
        if not self.state_version.strip():
            raise ValueError("plugin-owned state state_version must be non-blank")


class PluginStateStore(Protocol):
    async def load(self, plugin_id: str) -> PluginOwnedState | None: ...

    async def save(self, state: PluginOwnedState) -> None: ...

    async def delete(self, plugin_id: str) -> None: ...


class InMemoryPluginStateStore:
    """Deterministic reference adapter for the PluginStateStore contract."""

    def __init__(self) -> None:
        self._states: dict[str, PluginOwnedState] = {}

    async def load(self, plugin_id: str) -> PluginOwnedState | None:
        state = self._states.get(plugin_id)
        return deepcopy(state) if state is not None else None

    async def save(self, state: PluginOwnedState) -> None:
        self._states[state.plugin_id] = deepcopy(state)

    async def delete(self, plugin_id: str) -> None:
        self._states.pop(plugin_id, None)


class PluginStateMigration(Protocol):
    @property
    def spec(self) -> PluginStateMigrationSpec: ...

    async def migrate(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]: ...


class PluginStateMigrator:
    """Apply declared migrations atomically at the state-store boundary."""

    def __init__(self, *migrations: PluginStateMigration) -> None:
        self._migrations: dict[str, PluginStateMigration] = {}
        for migration in migrations:
            migration_id = migration.spec.migration_id
            if migration_id in self._migrations:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"duplicate plugin state migration hook {migration_id!r}",
                )
            self._migrations[migration_id] = migration

    async def migrate(
        self,
        manifest: PluginManifest,
        store: PluginStateStore,
    ) -> PluginOwnedState | None:
        state = await store.load(manifest.plugin_id)
        if state is None:
            return None
        if state.plugin_id != manifest.plugin_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "plugin state store returned state for the wrong plugin",
            )
        if state.state_version == manifest.state_version:
            return state

        path = self._migration_path(
            manifest,
            from_version=state.state_version,
            to_version=manifest.state_version,
        )
        payload = deepcopy(state.payload)
        for spec in path:
            migration = self._migrations.get(spec.migration_id)
            if migration is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    f"plugin state migration hook {spec.migration_id!r} is unavailable",
                )
            if migration.spec != spec:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    (
                        f"plugin state migration hook {spec.migration_id!r} does not match "
                        "the manifest"
                    ),
                )
            payload = await migration.migrate(deepcopy(payload))
            if not isinstance(payload, dict):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"plugin state migration {spec.migration_id!r} returned a non-object payload",
                )

        migrated = PluginOwnedState(
            plugin_id=manifest.plugin_id,
            state_version=manifest.state_version,
            payload=deepcopy(payload),
        )
        await store.save(migrated)
        return migrated

    @staticmethod
    def _migration_path(
        manifest: PluginManifest,
        *,
        from_version: str,
        to_version: str,
    ) -> tuple[PluginStateMigrationSpec, ...]:
        current = from_version
        visited = {current}
        path: list[PluginStateMigrationSpec] = []
        while current != to_version:
            candidates = [
                migration
                for migration in manifest.state_migrations
                if migration.from_version == current
            ]
            if len(candidates) != 1:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    (
                        f"plugin {manifest.plugin_id!r} requires one deterministic state migration "
                        f"from {current!r} toward {to_version!r}"
                    ),
                )
            migration = candidates[0]
            path.append(migration)
            current = migration.to_version
            if current in visited:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    f"plugin {manifest.plugin_id!r} contains a cyclic state migration path",
                )
            visited.add(current)
        return tuple(path)
