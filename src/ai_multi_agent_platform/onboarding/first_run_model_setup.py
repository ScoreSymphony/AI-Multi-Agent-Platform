"""Model-provider setup workflow for first-run onboarding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from ipaddress import ip_address
from typing import cast
from urllib.parse import urlsplit

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelProvider,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.models import (
    JsonModelRegistryStore,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.security import SecretReference

from .persistence import (
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    ModelProviderSetupRecord,
    OnboardingCommandRecord,
)
from .providers import OnboardingModelAdapter, OnboardingModelEndpoint

FIRST_RUN_RESOURCE_ID = "first-run"
ONBOARDING_COLLECTION = "onboarding"
ONBOARDING_CONFIGURE_MODEL_COMMAND = "onboarding.configure-model"
ONBOARDING_COMMANDS = (ONBOARDING_CONFIGURE_MODEL_COMMAND,)

_PLAINTEXT_CREDENTIAL_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "bearertoken",
        "credential",
        "password",
        "secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class ModelSetupPlan:
    """Validated setup inputs plus the provider instance to attach canonically."""

    adapter_id: str
    provider_id: str
    model_config_id: str
    provider_model: str
    display_name: str
    base_url: str
    location: ModelLocation
    credential_ref: SecretReference | None
    candidate_record: ModelProviderSetupRecord
    provider: ModelProvider
    capabilities: ModelCapabilities
    priority: int
    aliases: tuple[str, ...]
    adapter_metadata: tuple[AdapterMetadata, ...]


def validate_configure_command(
    context: RequestContext,
    resource_ref: str,
    payload: dict[str, JsonValue],
    command_records: dict[tuple[str, str], OnboardingCommandRecord],
) -> tuple[str, str, dict[str, JsonValue] | None]:
    """Validate command identity/idempotency and return any exact replay result."""

    if resource_ref != FIRST_RUN_RESOURCE_ID:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"onboarding model setup requires resource_ref={FIRST_RUN_RESOURCE_ID!r}",
        )
    _reject_credentials(payload)
    idempotency_key = context.idempotency_key
    if idempotency_key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "onboarding.configure-model requires an idempotency key",
        )
    payload_digest = _payload_digest(payload)
    replay_key = (context.actor.principal_ref, idempotency_key)
    replay = command_records.get(replay_key)
    if replay is None:
        return idempotency_key, payload_digest, None
    if (
        replay.command != ONBOARDING_CONFIGURE_MODEL_COMMAND
        or replay.resource_ref != resource_ref
        or replay.payload_digest != payload_digest
    ):
        raise ContractError(
            ErrorCode.CONFLICT,
            "idempotency key was already used for a different onboarding model command",
            details={"idempotency_key": idempotency_key},
        )
    return idempotency_key, payload_digest, dict(replay.result)


def build_model_setup_plan(
    payload: dict[str, JsonValue],
    *,
    model_adapters: dict[str, OnboardingModelAdapter],
    provider_records: dict[str, ModelProviderSetupRecord],
) -> ModelSetupPlan:
    """Parse one model-setup payload into a fully validated immutable plan."""

    adapter_id = _required_string(payload, "adapter_id")
    adapter = require_model_adapter(model_adapters, adapter_id)
    provider_id = _required_string(payload, "provider_id")
    model_config_id = _required_string(payload, "model_config_id")
    provider_model = _required_string(payload, "provider_model")
    display_name = _optional_string(payload, "display_name") or model_config_id
    base_url = _validated_base_url(_required_string(payload, "base_url"))
    location = _golden_path_location(_required_string(payload, "location"))
    if location is ModelLocation.LOCAL:
        _require_loopback_endpoint(base_url)

    current_record = provider_records.get(provider_id)
    credential_ref = _credential_reference(payload, current_record)
    mappings = dict(current_record.models) if current_record is not None else {}
    mappings[model_config_id] = provider_model
    candidate_record = ModelProviderSetupRecord(
        provider_id=provider_id,
        adapter_id=adapter_id,
        base_url=base_url,
        models=mappings,
        credential_ref=credential_ref,
    )
    provider = adapter.build_provider(
        OnboardingModelEndpoint(
            provider_id=provider_id,
            base_url=base_url,
            models=mappings,
            credential_ref=credential_ref,
        )
    )
    adapter_metadata = (
        AdapterMetadata(
            namespace=adapter_id,
            values={"provider_native_model": provider_model},
        ),
    )
    return ModelSetupPlan(
        adapter_id=adapter_id,
        provider_id=provider_id,
        model_config_id=model_config_id,
        provider_model=provider_model,
        display_name=display_name,
        base_url=base_url,
        location=location,
        credential_ref=credential_ref,
        candidate_record=candidate_record,
        provider=provider,
        capabilities=_capabilities(payload),
        priority=_optional_integer(payload, "priority", default=0),
        aliases=_string_tuple(payload, "aliases"),
        adapter_metadata=adapter_metadata,
    )


async def validate_model_endpoint(
    plan: ModelSetupPlan,
    *,
    model_adapters: dict[str, OnboardingModelAdapter],
) -> None:
    """Require endpoint health and explicit presence of the configured provider model."""

    adapter = require_model_adapter(model_adapters, plan.adapter_id)
    health = await plan.provider.health()
    try:
        native_models = await adapter.list_native_models(plan.provider)
    except ContractError:
        if health is HealthStatus.HEALTHY:
            raise
        raise _endpoint_unavailable(plan) from None
    if plan.provider_model not in native_models:
        raise ContractError(
            ErrorCode.MODEL_UNAVAILABLE,
            "configured provider model was not reported by the endpoint",
            provider_id=plan.provider_id,
            details={
                "provider_model": plan.provider_model,
                "available_provider_models": list(native_models),
            },
        )
    if health is not HealthStatus.HEALTHY:
        raise _endpoint_unavailable(plan)


def apply_model_setup(models: ModelRegistry, plan: ModelSetupPlan) -> None:
    """Apply one validated plan to the canonical model and provider registries."""

    _upsert_model_configuration(models, plan)
    provider_exists = any(
        item.descriptor.provider_id == plan.provider_id for item in models.list_providers()
    )
    if provider_exists:
        models.replace_provider(plan.provider)
    else:
        models.register_provider(plan.provider)


def persist_model_setup(
    plan: ModelSetupPlan,
    *,
    models: ModelRegistry,
    model_store: JsonModelRegistryStore,
    provider_store: JsonModelProviderSetupStore,
    provider_records: dict[str, ModelProviderSetupRecord],
) -> None:
    """Persist canonical model state and safe provider attachment metadata."""

    provider_records[plan.provider_id] = plan.candidate_record
    provider_store.save(provider_records.values())
    model_store.save(models)


def model_setup_result(plan: ModelSetupPlan) -> dict[str, JsonValue]:
    """Build the stable configure-model command result."""

    return {
        "id": plan.model_config_id,
        "type": "model",
        "provider_id": plan.provider_id,
        "adapter_id": plan.adapter_id,
        "display_name": plan.display_name,
        "location": plan.location.value,
        "health": HealthStatus.HEALTHY.value,
        "enabled": True,
        "external_paid_provider_selected": False,
        "credential_mode": "secret_reference" if plan.credential_ref is not None else "none",
    }


def record_configure_command(
    context: RequestContext,
    *,
    resource_ref: str,
    idempotency_key: str,
    payload_digest: str,
    result: dict[str, JsonValue],
    command_records: dict[tuple[str, str], OnboardingCommandRecord],
    command_store: JsonOnboardingCommandStore | None,
) -> None:
    """Record an exact configure-model replay after canonical persistence succeeds."""

    command_record = OnboardingCommandRecord(
        principal_ref=context.actor.principal_ref,
        idempotency_key=idempotency_key,
        command=ONBOARDING_CONFIGURE_MODEL_COMMAND,
        resource_ref=resource_ref,
        payload_digest=payload_digest,
        result=result,
    )
    command_records[command_record.replay_key] = command_record
    if command_store is not None:
        command_store.save(command_records.values())


def build_provider_from_record(
    record: ModelProviderSetupRecord,
    model_adapters: dict[str, OnboardingModelAdapter],
) -> ModelProvider:
    """Rebuild a provider attachment from persisted value-free setup metadata."""

    adapter = require_model_adapter(model_adapters, record.adapter_id)
    return adapter.build_provider(
        OnboardingModelEndpoint(
            provider_id=record.provider_id,
            base_url=record.base_url,
            models=record.models,
            credential_ref=record.credential_ref,
        )
    )


def require_model_adapter(
    model_adapters: dict[str, OnboardingModelAdapter],
    adapter_id: str,
) -> OnboardingModelAdapter:
    """Resolve an explicitly installed onboarding adapter or raise a canonical error."""

    try:
        return model_adapters[adapter_id]
    except KeyError as exc:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "requested first-run ModelProvider adapter is not installed",
            details={
                "adapter_id": adapter_id,
                "installed_adapter_ids": cast(JsonValue, sorted(model_adapters)),
            },
        ) from exc


def _credential_reference(
    payload: dict[str, JsonValue],
    current_record: ModelProviderSetupRecord | None,
) -> SecretReference | None:
    if "credential_ref" in payload:
        return _optional_secret_reference(payload.get("credential_ref"))
    if current_record is None:
        return None
    return current_record.credential_ref


def _endpoint_unavailable(plan: ModelSetupPlan) -> ContractError:
    return ContractError(
        ErrorCode.UNAVAILABLE,
        "model endpoint did not pass the first-run health check",
        retryable=True,
        provider_id=plan.provider_id,
        details={
            "provider_id": plan.provider_id,
            "location": plan.location.value,
            "guidance": (
                "Verify that the endpoint is running, that any referenced canonical secret "
                "is provisioned for the selected SecretProvider, and that the installed "
                "ModelProvider adapter can inspect its native model inventory."
            ),
        },
    )


def _upsert_model_configuration(models: ModelRegistry, plan: ModelSetupPlan) -> None:
    try:
        current_model = models.get_model(plan.model_config_id)
    except ContractError as exc:
        if exc.code is not ErrorCode.NOT_FOUND:
            raise
        models.register_model(
            ModelConfiguration(
                config_id=plan.model_config_id,
                display_name=plan.display_name,
                provider_id=plan.provider_id,
                aliases=plan.aliases,
                location=plan.location,
                health=HealthStatus.HEALTHY,
                priority=plan.priority,
                capabilities=plan.capabilities,
                adapter_metadata=plan.adapter_metadata,
            )
        )
        return
    if _model_configuration_matches(current_model, plan):
        return
    models.update_model(
        replace(
            current_model,
            display_name=plan.display_name,
            provider_id=plan.provider_id,
            revision=current_model.revision + 1,
            aliases=plan.aliases,
            location=plan.location,
            health=HealthStatus.HEALTHY,
            enabled=True,
            priority=plan.priority,
            capabilities=plan.capabilities,
            adapter_metadata=plan.adapter_metadata,
        )
    )


def _model_configuration_matches(current: ModelConfiguration, plan: ModelSetupPlan) -> bool:
    return (
        current.display_name == plan.display_name
        and current.provider_id == plan.provider_id
        and current.aliases == plan.aliases
        and current.location is plan.location
        and current.health is HealthStatus.HEALTHY
        and current.enabled
        and current.priority == plan.priority
        and current.capabilities == plan.capabilities
        and current.adapter_metadata == plan.adapter_metadata
    )


def _reject_credentials(value: JsonValue, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            field_path = f"{path}.{key}"
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if normalized in _PLAINTEXT_CREDENTIAL_KEYS and item is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "plaintext credentials are forbidden in onboarding configuration; use a "
                    "canonical #34 SecretReference",
                    details={"field": field_path},
                )
            _reject_credentials(item, path=field_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_credentials(item, path=f"{path}[{index}]")


def _optional_secret_reference(value: JsonValue | None) -> SecretReference | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref must be a canonical SecretReference object",
        )
    provider = value.get("provider")
    secret_id = value.get("secret_id")
    scope = value.get("scope")
    version = value.get("version")
    metadata = value.get("metadata", {})
    for field_name, field_value in (
        ("provider", provider),
        ("secret_id", secret_id),
        ("scope", scope),
    ):
        if not isinstance(field_value, str) or not field_value.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"credential_ref.{field_name} must be a non-blank string",
            )
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref.version must be a non-blank string when provided",
        )
    if not isinstance(metadata, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref.metadata must be a JSON object",
        )
    return SecretReference(
        provider=cast(str, provider),
        secret_id=cast(str, secret_id),
        scope=cast(str, scope),
        version=version,
        metadata=metadata,
    )


def _payload_digest(payload: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must be an explicit http(s) endpoint",
            details={"field": "base_url"},
        )
    if parsed.username is not None or parsed.password is not None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must not embed credentials",
            details={"field": "base_url"},
        )
    if parsed.query or parsed.fragment:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must not contain query or fragment components",
            details={"field": "base_url"},
        )
    return value.rstrip("/")


def _require_loopback_endpoint(base_url: str) -> None:
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        raise AssertionError("validated URL must have a hostname")
    if hostname.casefold() == "localhost":
        return
    try:
        address = ip_address(hostname)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location='local' requires a loopback endpoint; use self_hosted for an explicitly "
            "remote self-managed endpoint",
            details={"base_url": base_url},
        ) from exc
    if not address.is_loopback:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location='local' requires a loopback endpoint; use self_hosted for an explicitly "
            "remote self-managed endpoint",
            details={"base_url": base_url},
        )


def _golden_path_location(value: str) -> ModelLocation:
    try:
        location = ModelLocation(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location must be local or self_hosted for first-run model setup",
            details={"location": value},
        ) from exc
    if location is ModelLocation.REMOTE:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "remote/paid providers are not configured by the first-run golden path; select them "
            "explicitly through normal model administration instead",
            details={"location": value},
        )
    return location


def _capabilities(payload: dict[str, JsonValue]) -> ModelCapabilities:
    raw = payload.get("capabilities")
    if raw is None:
        return ModelCapabilities(modalities=("text",))
    if not isinstance(raw, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "capabilities must be a JSON object")
    context_window = raw.get("context_window")
    if context_window is not None and (
        isinstance(context_window, bool) or not isinstance(context_window, int)
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "capabilities.context_window must be an integer",
        )
    return ModelCapabilities(
        context_window=context_window,
        tool_calling=_mapping_boolean(raw, "tool_calling"),
        structured_output=_mapping_boolean(raw, "structured_output"),
        streaming=_mapping_boolean(raw, "streaming"),
        modalities=_mapping_string_tuple(raw, "modalities", default=("text",)),
        reasoning=_mapping_string_tuple(raw, "reasoning", default=()),
    )


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_integer(payload: dict[str, JsonValue], key: str, *, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an integer")
    return value


def _string_tuple(payload: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a list of strings")
    return tuple(item for item in raw if isinstance(item, str))


def _mapping_boolean(payload: dict[str, JsonValue], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"capabilities.{key} must be a boolean")
    return value


def _mapping_string_tuple(
    payload: dict[str, JsonValue],
    key: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"capabilities.{key} must be a list of strings",
        )
    return tuple(item for item in value if isinstance(item, str))
