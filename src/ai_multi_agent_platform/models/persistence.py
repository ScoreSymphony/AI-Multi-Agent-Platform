"""JSON persistence for canonical model inventory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import AdapterMetadata, HealthStatus, JsonValue

from .registry import ModelRegistry
from .types import ModelCapabilities, ModelConfiguration, ModelLocation

MODEL_REGISTRY_SCHEMA_VERSION = "1"


class JsonModelRegistryStore:
    """Persist canonical model configurations without serializing live providers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, registry: ModelRegistry) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": MODEL_REGISTRY_SCHEMA_VERSION,
            "models": [_model_to_json(model) for model in registry.list_models()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load_models(self) -> tuple[ModelConfiguration, ...]:
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _json_object(raw, "registry document")
        schema_version = _required_string(document, "schema_version")
        if schema_version != MODEL_REGISTRY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported model registry schema version: "
                f"{schema_version!r}; expected {MODEL_REGISTRY_SCHEMA_VERSION!r}"
            )

        raw_models = document.get("models")
        if not isinstance(raw_models, list):
            raise ValueError("model registry document must contain a models list")
        return tuple(_model_from_json(item) for item in raw_models)

    def restore(self, registry: ModelRegistry) -> tuple[ModelConfiguration, ...]:
        models = self.load_models()
        for model in models:
            registry.register_model(model)
        return models


def _model_to_json(model: ModelConfiguration) -> dict[str, JsonValue]:
    return {
        "config_id": model.config_id,
        "display_name": model.display_name,
        "provider_id": model.provider_id,
        "revision": model.revision,
        "aliases": list(model.aliases),
        "location": model.location.value,
        "node_ref": model.node_ref,
        "health": model.health.value,
        "enabled": model.enabled,
        "priority": model.priority,
        "capabilities": {
            "context_window": model.capabilities.context_window,
            "tool_calling": model.capabilities.tool_calling,
            "structured_output": model.capabilities.structured_output,
            "streaming": model.capabilities.streaming,
            "modalities": list(model.capabilities.modalities),
            "reasoning": list(model.capabilities.reasoning),
        },
        "resource_hints": dict(model.resource_hints),
        "cost_metadata": dict(model.cost_metadata),
        "adapter_metadata": [
            {
                "namespace": metadata.namespace,
                "values": dict(metadata.values),
            }
            for metadata in model.adapter_metadata
        ],
    }


def _model_from_json(value: object) -> ModelConfiguration:
    data = _json_object(value, "model configuration")
    capabilities_data = _json_object(data.get("capabilities"), "capabilities")
    context_window = capabilities_data.get("context_window")
    if context_window is not None and (
        isinstance(context_window, bool) or not isinstance(context_window, int)
    ):
        raise ValueError("capabilities.context_window must be an integer or null")

    return ModelConfiguration(
        config_id=_required_string(data, "config_id"),
        display_name=_required_string(data, "display_name"),
        provider_id=_required_string(data, "provider_id"),
        revision=_required_integer(data, "revision"),
        aliases=_string_tuple(data.get("aliases"), "aliases"),
        location=ModelLocation(_required_string(data, "location")),
        node_ref=_optional_string(data, "node_ref"),
        health=HealthStatus(_required_string(data, "health")),
        enabled=_required_boolean(data, "enabled"),
        priority=_required_integer(data, "priority"),
        capabilities=ModelCapabilities(
            context_window=context_window,
            tool_calling=_required_boolean(capabilities_data, "tool_calling"),
            structured_output=_required_boolean(
                capabilities_data,
                "structured_output",
            ),
            streaming=_required_boolean(capabilities_data, "streaming"),
            modalities=_string_tuple(capabilities_data.get("modalities"), "modalities"),
            reasoning=_string_tuple(capabilities_data.get("reasoning"), "reasoning"),
        ),
        resource_hints=_json_object(data.get("resource_hints"), "resource_hints"),
        cost_metadata=_json_object(data.get("cost_metadata"), "cost_metadata"),
        adapter_metadata=_adapter_metadata(data.get("adapter_metadata")),
    )


def _adapter_metadata(value: JsonValue | None) -> tuple[AdapterMetadata, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("adapter_metadata must be a list")

    entries: list[AdapterMetadata] = []
    for raw_entry in value:
        entry = _json_object(raw_entry, "adapter metadata")
        entries.append(
            AdapterMetadata(
                namespace=_required_string(entry, "namespace"),
                values=_json_object(entry.get("values"), "adapter metadata values"),
            )
        )
    return tuple(entries)


def _json_object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False


def _required_string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _required_integer(data: dict[str, JsonValue], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_boolean(data: dict[str, JsonValue], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _string_tuple(value: JsonValue | None, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(value)
