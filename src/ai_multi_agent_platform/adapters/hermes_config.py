"""Configuration and compatibility policy for the optional Hermes adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.configuration import ConfigurationSchema

HERMES_UPSTREAM_REPOSITORY = "https://github.com/NousResearch/hermes-agent"
HERMES_PINNED_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"
HERMES_ADAPTER_ID = "hermes-api-server"


class HermesRuntimeMode(StrEnum):
    """Supported Hermes deployment/runtime modes."""

    API_SERVER = "api_server"


class HermesRetryBehavior(StrEnum):
    """Retry ownership at the Hermes adapter boundary."""

    PLATFORM_OWNED = "platform_owned"


class HermesBridgeMode(StrEnum):
    """Canonical model/capability mapping policy."""

    STRICT = "strict"


class HermesDiagnosticsMode(StrEnum):
    """Diagnostics/logging ownership for the adapter."""

    PLATFORM_ONLY = "platform_only"


class HermesCompatibilityStatus(StrEnum):
    """Declared compatibility status for the configured upstream revision."""

    VERIFIED_PIN = "verified_pin"
    UNVERIFIED_PIN = "unverified_pin"


HERMES_CONFIGURATION_SCHEMA = ConfigurationSchema(
    version="hermes-adapter-v1",
    json_schema={
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean"},
            "base_url": {"type": "string", "minLength": 1},
            "api_key_env": {"type": "string", "minLength": 1},
            "pinned_revision": {"type": "string", "minLength": 1},
            "request_timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
            "plan_timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
            "poll_interval_seconds": {"type": "number", "exclusiveMinimum": 0},
            "profile": {"type": ["string", "null"], "minLength": 1},
            "runtime_mode": {"const": HermesRuntimeMode.API_SERVER.value},
            "retry_behavior": {"const": HermesRetryBehavior.PLATFORM_OWNED.value},
            "bridge_mode": {"const": HermesBridgeMode.STRICT.value},
            "diagnostics_mode": {"const": HermesDiagnosticsMode.PLATFORM_ONLY.value},
            "compatibility_status": {
                "enum": [
                    HermesCompatibilityStatus.VERIFIED_PIN.value,
                    HermesCompatibilityStatus.UNVERIFIED_PIN.value,
                ]
            },
            "model_bridge": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_id": {"type": "string", "minLength": 1},
                        "hermes_target": {"type": "string", "minLength": 1},
                    },
                    "required": ["canonical_id", "hermes_target"],
                    "additionalProperties": False,
                },
            },
            "capability_bridge": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_id": {"type": "string", "minLength": 1},
                        "hermes_target": {"type": "string", "minLength": 1},
                    },
                    "required": ["canonical_id", "hermes_target"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class HermesAdapterConfig:
    """Configuration for the external Hermes API-server adapter.

    Secrets remain outside the configuration object: ``api_key_env`` names an
    environment/secret reference resolved only when a request is emitted.
    """

    enabled: bool = False
    base_url: str = "http://127.0.0.1:8642"
    api_key_env: str = "API_SERVER_KEY"
    pinned_revision: str = HERMES_PINNED_REVISION
    request_timeout_seconds: float = 10.0
    plan_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 0.2
    profile: str | None = None
    runtime_mode: HermesRuntimeMode = HermesRuntimeMode.API_SERVER
    retry_behavior: HermesRetryBehavior = HermesRetryBehavior.PLATFORM_OWNED
    bridge_mode: HermesBridgeMode = HermesBridgeMode.STRICT
    diagnostics_mode: HermesDiagnosticsMode = HermesDiagnosticsMode.PLATFORM_ONLY
    compatibility_status: HermesCompatibilityStatus = HermesCompatibilityStatus.VERIFIED_PIN
    model_bridge: Mapping[str, str] = field(default_factory=dict)
    capability_bridge: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("Hermes base_url must not be blank")
        if not self.api_key_env.strip():
            raise ValueError("Hermes api_key_env must not be blank")
        if not self.pinned_revision.strip():
            raise ValueError("Hermes pinned_revision must not be blank")
        if self.request_timeout_seconds <= 0:
            raise ValueError("Hermes request_timeout_seconds must be greater than zero")
        if self.plan_timeout_seconds <= 0:
            raise ValueError("Hermes plan_timeout_seconds must be greater than zero")
        if self.poll_interval_seconds <= 0:
            raise ValueError("Hermes poll_interval_seconds must be greater than zero")
        if self.profile is not None and not self.profile.strip():
            raise ValueError("Hermes profile must not be blank")
        if not isinstance(self.runtime_mode, HermesRuntimeMode):
            raise ValueError("Hermes runtime_mode must be a HermesRuntimeMode")
        if not isinstance(self.retry_behavior, HermesRetryBehavior):
            raise ValueError("Hermes retry_behavior must be a HermesRetryBehavior")
        if not isinstance(self.bridge_mode, HermesBridgeMode):
            raise ValueError("Hermes bridge_mode must be a HermesBridgeMode")
        if not isinstance(self.diagnostics_mode, HermesDiagnosticsMode):
            raise ValueError("Hermes diagnostics_mode must be a HermesDiagnosticsMode")
        if not isinstance(self.compatibility_status, HermesCompatibilityStatus):
            raise ValueError("Hermes compatibility_status must be a HermesCompatibilityStatus")
        if (
            self.compatibility_status is HermesCompatibilityStatus.VERIFIED_PIN
            and self.pinned_revision != HERMES_PINNED_REVISION
        ):
            raise ValueError(
                "Hermes compatibility_status=verified_pin requires the repository-tested pin"
            )
        if (
            self.compatibility_status is HermesCompatibilityStatus.UNVERIFIED_PIN
            and self.pinned_revision == HERMES_PINNED_REVISION
        ):
            raise ValueError(
                "Hermes repository-tested pin must use compatibility_status=verified_pin"
            )
        if any(not key.strip() or not value.strip() for key, value in self.model_bridge.items()):
            raise ValueError("Hermes model_bridge keys and values must not be blank")
        if any(
            not key.strip() or not value.strip() for key, value in self.capability_bridge.items()
        ):
            raise ValueError("Hermes capability_bridge keys and values must not be blank")
        object.__setattr__(self, "model_bridge", MappingProxyType(dict(self.model_bridge)))
        object.__setattr__(
            self,
            "capability_bridge",
            MappingProxyType(dict(self.capability_bridge)),
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> HermesAdapterConfig:
        """Build strict validated Hermes configuration from resolved JSON-like values."""

        allowed = {
            "enabled",
            "base_url",
            "api_key_env",
            "pinned_revision",
            "request_timeout_seconds",
            "plan_timeout_seconds",
            "poll_interval_seconds",
            "profile",
            "runtime_mode",
            "retry_behavior",
            "bridge_mode",
            "diagnostics_mode",
            "compatibility_status",
            "model_bridge",
            "capability_bridge",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown Hermes configuration fields: {unknown!r}")

        def string_field(name: str, default: str) -> str:
            raw = values.get(name, default)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"{name} must be a non-blank string")
            return raw

        def number_field(name: str, default: float) -> float:
            raw = values.get(name, default)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{name} must be numeric")
            return float(raw)

        def enum_field(name: str, enum_type: type[StrEnum], default: StrEnum) -> StrEnum:
            raw = values.get(name, default.value)
            if not isinstance(raw, str):
                raise ValueError(f"{name} must be a string")
            try:
                return enum_type(raw)
            except ValueError as exc:
                raise ValueError(f"unsupported Hermes {name}: {raw}") from exc

        def bridge_field(name: str) -> dict[str, str]:
            raw = values.get(name, [])
            if not isinstance(raw, list):
                raise ValueError(f"{name} must be an array of bridge entries")
            result: dict[str, str] = {}
            for index, raw_entry in enumerate(raw):
                if not isinstance(raw_entry, Mapping):
                    raise ValueError(f"{name}[{index}] must be an object")
                unknown_entry_fields = sorted(set(raw_entry) - {"canonical_id", "hermes_target"})
                if unknown_entry_fields:
                    raise ValueError(
                        f"{name}[{index}] has unknown fields: {unknown_entry_fields!r}"
                    )
                canonical_id = raw_entry.get("canonical_id")
                hermes_target = raw_entry.get("hermes_target")
                if not isinstance(canonical_id, str) or not canonical_id.strip():
                    raise ValueError(f"{name}[{index}].canonical_id must be non-blank")
                if not isinstance(hermes_target, str) or not hermes_target.strip():
                    raise ValueError(f"{name}[{index}].hermes_target must be non-blank")
                if canonical_id in result:
                    raise ValueError(f"{name} contains duplicate canonical_id {canonical_id!r}")
                result[canonical_id] = hermes_target
            return result

        enabled = values.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        profile_raw = values.get("profile")
        if profile_raw is not None and not isinstance(profile_raw, str):
            raise ValueError("profile must be a string or null")

        return cls(
            enabled=enabled,
            base_url=string_field("base_url", "http://127.0.0.1:8642"),
            api_key_env=string_field("api_key_env", "API_SERVER_KEY"),
            pinned_revision=string_field("pinned_revision", HERMES_PINNED_REVISION),
            request_timeout_seconds=number_field("request_timeout_seconds", 10.0),
            plan_timeout_seconds=number_field("plan_timeout_seconds", 120.0),
            poll_interval_seconds=number_field("poll_interval_seconds", 0.2),
            profile=profile_raw,
            runtime_mode=cast(
                HermesRuntimeMode,
                enum_field("runtime_mode", HermesRuntimeMode, HermesRuntimeMode.API_SERVER),
            ),
            retry_behavior=cast(
                HermesRetryBehavior,
                enum_field(
                    "retry_behavior",
                    HermesRetryBehavior,
                    HermesRetryBehavior.PLATFORM_OWNED,
                ),
            ),
            bridge_mode=cast(
                HermesBridgeMode,
                enum_field("bridge_mode", HermesBridgeMode, HermesBridgeMode.STRICT),
            ),
            diagnostics_mode=cast(
                HermesDiagnosticsMode,
                enum_field(
                    "diagnostics_mode",
                    HermesDiagnosticsMode,
                    HermesDiagnosticsMode.PLATFORM_ONLY,
                ),
            ),
            compatibility_status=cast(
                HermesCompatibilityStatus,
                enum_field(
                    "compatibility_status",
                    HermesCompatibilityStatus,
                    HermesCompatibilityStatus.VERIFIED_PIN,
                ),
            ),
            model_bridge=bridge_field("model_bridge"),
            capability_bridge=bridge_field("capability_bridge"),
        )
