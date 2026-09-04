"""Safe persistence for first-run onboarding configuration.

Only non-secret adapter metadata is stored here. Credential material is intentionally
excluded; authenticated provider setups persist only canonical #34 ``SecretReference``
metadata and resolve the referenced value at the adapter boundary. Idempotency replay
records persist only payload digests plus already-redacted command responses.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import SecretReference

ONBOARDING_PROVIDER_SCHEMA_VERSION = "1"
ONBOARDING_COMMAND_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ModelProviderSetupRecord:
    """Restart-safe, value-free configuration for one installed provider adapter."""

    provider_id: str
    adapter_id: str
    base_url: str
    models: Mapping[str, str]
    credential_ref: SecretReference | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not self.adapter_id.strip():
            raise ValueError("adapter_id must not be blank")
        if not self.base_url.strip():
            raise ValueError("base_url must not be blank")
        if not self.models:
            raise ValueError("provider setup requires at least one model mapping")
        if any(not key.strip() or not value.strip() for key, value in self.models.items()):
            raise ValueError("provider model mappings must contain non-blank strings")
        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "provider_id": self.provider_id,
            "adapter_id": self.adapter_id,
            "base_url": self.base_url,
            "models": dict(self.models),
        }
        if self.credential_ref is not None:
            payload["credential_ref"] = self.credential_ref.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class OnboardingCommandRecord:
    """Value-safe persistent replay metadata for one mutating onboarding command."""

    principal_ref: str
    idempotency_key: str
    command: str
    resource_ref: str
    payload_digest: str
    result: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        for field_name in (
            "principal_ref",
            "idempotency_key",
            "command",
            "resource_ref",
            "payload_digest",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))

    @property
    def replay_key(self) -> tuple[str, str]:
        return self.principal_ref, self.idempotency_key

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "principal_ref": self.principal_ref,
            "idempotency_key": self.idempotency_key,
            "command": self.command,
            "resource_ref": self.resource_ref,
            "payload_digest": self.payload_digest,
            "result": dict(self.result),
        }


class JsonModelProviderSetupStore:
    """Atomic JSON persistence for safe adapter setup metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[ModelProviderSetupRecord, ...]:
        if not self.path.exists():
            return ()
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _json_object(raw, "provider setup document")
        schema_version = _required_string(document, "schema_version")
        if schema_version != ONBOARDING_PROVIDER_SCHEMA_VERSION:
            raise ValueError(
                "unsupported onboarding provider schema version: "
                f"{schema_version!r}; expected {ONBOARDING_PROVIDER_SCHEMA_VERSION!r}"
            )
        providers = document.get("providers")
        if not isinstance(providers, list):
            raise ValueError("provider setup document must contain a providers list")
        records = tuple(_record_from_json(item) for item in providers)
        provider_ids = [record.provider_id for record in records]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider setup document contains duplicate provider IDs")
        return tuple(sorted(records, key=lambda item: item.provider_id))

    def save(self, records: Iterable[ModelProviderSetupRecord]) -> None:
        ordered = tuple(sorted(records, key=lambda item: item.provider_id))
        provider_ids = [record.provider_id for record in ordered]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider setup records contain duplicate provider IDs")
        document: dict[str, JsonValue] = {
            "schema_version": ONBOARDING_PROVIDER_SCHEMA_VERSION,
            "providers": [record.to_json() for record in ordered],
        }
        _atomic_write_json(self.path, document)


class JsonOnboardingCommandStore:
    """Atomic JSON persistence for redacted onboarding command replay records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> tuple[OnboardingCommandRecord, ...]:
        if not self.path.exists():
            return ()
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _json_object(raw, "onboarding command document")
        schema_version = _required_string(document, "schema_version")
        if schema_version != ONBOARDING_COMMAND_SCHEMA_VERSION:
            raise ValueError(
                "unsupported onboarding command schema version: "
                f"{schema_version!r}; expected {ONBOARDING_COMMAND_SCHEMA_VERSION!r}"
            )
        commands = document.get("commands")
        if not isinstance(commands, list):
            raise ValueError("onboarding command document must contain a commands list")
        records = tuple(_command_record_from_json(item) for item in commands)
        replay_keys = [record.replay_key for record in records]
        if len(replay_keys) != len(set(replay_keys)):
            raise ValueError("onboarding command document contains duplicate replay keys")
        return tuple(sorted(records, key=lambda item: item.replay_key))

    def save(self, records: Iterable[OnboardingCommandRecord]) -> None:
        ordered = tuple(sorted(records, key=lambda item: item.replay_key))
        replay_keys = [record.replay_key for record in ordered]
        if len(replay_keys) != len(set(replay_keys)):
            raise ValueError("onboarding command records contain duplicate replay keys")
        document: dict[str, JsonValue] = {
            "schema_version": ONBOARDING_COMMAND_SCHEMA_VERSION,
            "commands": [record.to_json() for record in ordered],
        }
        _atomic_write_json(self.path, document)


def _record_from_json(value: object) -> ModelProviderSetupRecord:
    data = _json_object(value, "provider setup")
    raw_models = _json_object(data.get("models"), "provider model mappings")
    models: dict[str, str] = {}
    for key, value in raw_models.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError("provider model mappings must contain string values")
        models[key] = value
    return ModelProviderSetupRecord(
        provider_id=_required_string(data, "provider_id"),
        adapter_id=_required_string(data, "adapter_id"),
        base_url=_required_string(data, "base_url"),
        models=models,
        credential_ref=_optional_secret_reference(data.get("credential_ref")),
    )


def _command_record_from_json(value: object) -> OnboardingCommandRecord:
    data = _json_object(value, "onboarding command record")
    result = _json_object(data.get("result"), "onboarding command result")
    return OnboardingCommandRecord(
        principal_ref=_required_string(data, "principal_ref"),
        idempotency_key=_required_string(data, "idempotency_key"),
        command=_required_string(data, "command"),
        resource_ref=_required_string(data, "resource_ref"),
        payload_digest=_required_string(data, "payload_digest"),
        result=result,
    )


def _optional_secret_reference(value: JsonValue | None) -> SecretReference | None:
    if value is None:
        return None
    data = _json_object(value, "credential_ref")
    version_value = data.get("version")
    if version_value is not None and (
        not isinstance(version_value, str) or not version_value.strip()
    ):
        raise ValueError("credential_ref.version must be a non-blank string when provided")
    metadata_value = data.get("metadata", {})
    metadata = _json_object(metadata_value, "credential_ref.metadata")
    return SecretReference(
        provider=_required_string(data, "provider"),
        secret_id=_required_string(data, "secret_id"),
        scope=_required_string(data, "scope"),
        version=version_value,
        metadata=metadata,
    )


def _atomic_write_json(path: Path, document: dict[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _json_object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _required_string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
