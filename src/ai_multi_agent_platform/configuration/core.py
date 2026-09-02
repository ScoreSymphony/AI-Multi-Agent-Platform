"""Deterministic, schema-validated platform configuration resolution."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from jsonschema import Draft202012Validator

from .secrets import SecretReference

JsonObject = dict[str, Any]


class ConfigScope(StrEnum):
    PLATFORM_DEFAULTS = "platform_defaults"
    DEPLOYMENT = "deployment"
    ORGANIZATION = "organization"
    TEAM = "team"
    PROJECT = "project"
    WORKSPACE = "workspace"
    AGENT = "agent"
    AGENT_TEAM = "agent_team"
    ADAPTER = "adapter"
    PROVIDER = "provider"
    CONNECTOR = "connector"
    TASK_RUN_OVERRIDE = "task_run_override"


CONFIG_PRECEDENCE: tuple[ConfigScope, ...] = (
    ConfigScope.PLATFORM_DEFAULTS,
    ConfigScope.DEPLOYMENT,
    ConfigScope.ORGANIZATION,
    ConfigScope.TEAM,
    ConfigScope.PROJECT,
    ConfigScope.WORKSPACE,
    ConfigScope.AGENT,
    ConfigScope.AGENT_TEAM,
    ConfigScope.ADAPTER,
    ConfigScope.PROVIDER,
    ConfigScope.CONNECTOR,
    ConfigScope.TASK_RUN_OVERRIDE,
)


class ReloadRequirement(StrEnum):
    LIVE = "live"
    RELOAD = "reload"
    RESTART = "restart"


@dataclass(frozen=True, slots=True)
class ConfigSource:
    source_id: str
    source_type: str
    description: str = ""

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("configuration source_id must not be blank")
        if not self.source_type.strip():
            raise ValueError("configuration source_type must not be blank")


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    scope: ConfigScope
    values: Mapping[str, Any]
    source: ConfigSource


@dataclass(frozen=True, slots=True)
class ConfigurationSchema:
    version: str
    json_schema: Mapping[str, Any]
    secret_paths: frozenset[str] = frozenset()
    run_override_paths: frozenset[str] = frozenset()
    reload_requirements: Mapping[str, ReloadRequirement] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("configuration schema version must not be blank")
        Draft202012Validator.check_schema(dict(self.json_schema))


@dataclass(frozen=True, slots=True)
class ConfigProvenance:
    scope: ConfigScope
    source_id: str
    source_type: str


@dataclass(frozen=True, slots=True)
class EffectiveConfigEntry:
    path: str
    value: Any
    provenance: tuple[ConfigProvenance, ...]
    reload_requirement: ReloadRequirement
    is_secret: bool

    def safe_value(self) -> Any:
        if self.is_secret:
            if isinstance(self.value, SecretReference):
                return {"configured": True, "secret_ref": self.value.to_dict()}
            return {"configured": False}
        return _safe_serialize(self.value)


@dataclass(frozen=True, slots=True)
class EffectiveConfiguration:
    schema_version: str
    values: Mapping[str, Any]
    entries: Mapping[str, EffectiveConfigEntry]

    def as_dict(self) -> JsonObject:
        return cast(JsonObject, _safe_serialize(self.values))

    def inspect(self) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.entries):
            entry = self.entries[path]
            rows.append(
                {
                    "path": path,
                    "value": entry.safe_value(),
                    "configured": entry.value is not None,
                    "is_secret": entry.is_secret,
                    "schema_version": self.schema_version,
                    "validation_status": "valid",
                    "reload_requirement": entry.reload_requirement.value,
                    "source_chain": [
                        {
                            "scope": item.scope.value,
                            "source_id": item.source_id,
                            "source_type": item.source_type,
                        }
                        for item in entry.provenance
                    ],
                }
            )
        return tuple(rows)


class ConfigurationError(ValueError):
    """Raised when configuration cannot be safely resolved or validated."""


class ConfigurationResolver:
    def __init__(self, schema: ConfigurationSchema) -> None:
        self._schema = schema
        self._validator = Draft202012Validator(dict(schema.json_schema))

    def resolve(self, layers: tuple[ConfigLayer, ...]) -> EffectiveConfiguration:
        precedence = {scope: index for index, scope in enumerate(CONFIG_PRECEDENCE)}
        ordered = sorted(enumerate(layers), key=lambda item: (precedence[item[1].scope], item[0]))
        merged: dict[str, Any] = {}
        chains: dict[str, list[ConfigProvenance]] = {}

        for _, layer in ordered:
            flattened = _flatten(layer.values)
            if layer.scope is ConfigScope.TASK_RUN_OVERRIDE:
                forbidden = set(flattened) - set(self._schema.run_override_paths)
                if forbidden:
                    paths = sorted(forbidden)
                    raise ConfigurationError(
                        f"run override is not allowed for configuration paths: {paths!r}"
                    )
            for path, value in flattened.items():
                _set_path(merged, path, value)
                chains.setdefault(path, []).append(
                    ConfigProvenance(layer.scope, layer.source.source_id, layer.source.source_type)
                )

        for path in self._schema.secret_paths:
            value = _get_path(merged, path)
            if value is not None and not isinstance(value, SecretReference):
                raise ConfigurationError(f"{path!r} must contain a SecretReference, not plaintext")

        safe = _safe_serialize(merged)
        errors = sorted(
            self._validator.iter_errors(safe),
            key=lambda error: tuple(str(part) for part in error.path),
        )
        if errors:
            details = "; ".join(error.message for error in errors)
            raise ConfigurationError(f"configuration validation failed: {details}")

        entries: dict[str, EffectiveConfigEntry] = {}
        for path, value in _flatten(merged).items():
            entries[path] = EffectiveConfigEntry(
                path=path,
                value=value,
                provenance=tuple(chains.get(path, ())),
                reload_requirement=self._schema.reload_requirements.get(
                    path, ReloadRequirement.RESTART
                ),
                is_secret=path in self._schema.secret_paths,
            )
        return EffectiveConfiguration(self._schema.version, merged, entries)


def environment_layer(
    *,
    source_id: str,
    environ: Mapping[str, str],
    key_map: Mapping[str, str],
    scope: ConfigScope = ConfigScope.DEPLOYMENT,
) -> ConfigLayer:
    """Create an explicit deployment layer from a caller-approved environment mapping."""
    values: dict[str, Any] = {}
    for env_name, config_path in key_map.items():
        if env_name in environ:
            _set_path(values, config_path, environ[env_name])
    return ConfigLayer(scope, values, ConfigSource(source_id, "environment"))


def _flatten(values: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping) and not isinstance(value, SecretReference):
            result.update(_flatten(value, path))
        else:
            result[path] = value
    return result


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def _get_path(values: Mapping[str, Any], path: str) -> Any:
    current: Any = values
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _safe_serialize(value: Any) -> Any:
    if isinstance(value, SecretReference):
        return {"secret_ref": value.to_dict()}
    if isinstance(value, Mapping):
        return {str(key): _safe_serialize(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_safe_serialize(item) for item in value]
    return value
