"""Model-provider setup workflow for first-run onboarding."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

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

from .first_run_model_validation import (
    capabilities,
    golden_path_location,
    optional_integer,
    optional_secret_reference,
    optional_string,
    payload_digest,
    reject_plaintext_credentials,
    require_loopback_endpoint,
    required_string,
    string_tuple,
    validated_base_url,
)
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


@dataclass(frozen=True, slots=True)
class ModelSetupPlan:
    """Validated endpoint inputs plus the provider instance to attach canonically."""

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


@dataclass(frozen=True, slots=True)
class ModelConfigurationInput:
    """Model configuration fields parsed only after endpoint validation succeeds."""

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
    reject_plaintext_credentials(payload)
    idempotency_key = context.idempotency_key
    if idempotency_key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "onboarding.configure-model requires an idempotency key",
        )
    digest = payload_digest(payload)
    replay_key = (context.actor.principal_ref, idempotency_key)
    replay = command_records.get(replay_key)
    if replay is None:
        return idempotency_key, digest, None
    if (
        replay.command != ONBOARDING_CONFIGURE_MODEL_COMMAND
        or replay.resource_ref != resource_ref
        or replay.payload_digest != digest
    ):
        raise ContractError(
            ErrorCode.CONFLICT,
            "idempotency key was already used for a different onboarding model command",
            details={"idempotency_key": idempotency_key},
        )
    return idempotency_key, digest, dict(replay.result)


def build_model_setup_plan(
    payload: dict[str, JsonValue],
    *,
    model_adapters: dict[str, OnboardingModelAdapter],
    provider_records: dict[str, ModelProviderSetupRecord],
) -> ModelSetupPlan:
    """Parse endpoint/provider inputs without performing remote validation."""

    adapter_id = required_string(payload, "adapter_id")
    adapter = require_model_adapter(model_adapters, adapter_id)
    provider_id = required_string(payload, "provider_id")
    model_config_id = required_string(payload, "model_config_id")
    provider_model = required_string(payload, "provider_model")
    display_name = optional_string(payload, "display_name") or model_config_id
    base_url = validated_base_url(required_string(payload, "base_url"))
    location = golden_path_location(required_string(payload, "location"))
    if location is ModelLocation.LOCAL:
        require_loopback_endpoint(base_url)

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


def apply_model_setup(
    models: ModelRegistry,
    plan: ModelSetupPlan,
    payload: dict[str, JsonValue],
) -> None:
    """Parse model fields, then apply the validated plan to canonical registries."""

    configuration = _model_configuration_input(payload, plan)
    _upsert_model_configuration(models, plan, configuration)
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
        return optional_secret_reference(payload.get("credential_ref"))
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


def _model_configuration_input(
    payload: dict[str, JsonValue],
    plan: ModelSetupPlan,
) -> ModelConfigurationInput:
    return ModelConfigurationInput(
        capabilities=capabilities(payload),
        priority=optional_integer(payload, "priority", default=0),
        aliases=string_tuple(payload, "aliases"),
        adapter_metadata=(
            AdapterMetadata(
                namespace=plan.adapter_id,
                values={"provider_native_model": plan.provider_model},
            ),
        ),
    )


def _upsert_model_configuration(
    models: ModelRegistry,
    plan: ModelSetupPlan,
    configuration: ModelConfigurationInput,
) -> None:
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
                aliases=configuration.aliases,
                location=plan.location,
                health=HealthStatus.HEALTHY,
                priority=configuration.priority,
                capabilities=configuration.capabilities,
                adapter_metadata=configuration.adapter_metadata,
            )
        )
        return
    if _model_configuration_matches(current_model, plan, configuration):
        return
    models.update_model(
        replace(
            current_model,
            display_name=plan.display_name,
            provider_id=plan.provider_id,
            revision=current_model.revision + 1,
            aliases=configuration.aliases,
            location=plan.location,
            health=HealthStatus.HEALTHY,
            enabled=True,
            priority=configuration.priority,
            capabilities=configuration.capabilities,
            adapter_metadata=configuration.adapter_metadata,
        )
    )


def _model_configuration_matches(
    current: ModelConfiguration,
    plan: ModelSetupPlan,
    configuration: ModelConfigurationInput,
) -> bool:
    return (
        current.display_name == plan.display_name
        and current.provider_id == plan.provider_id
        and current.aliases == configuration.aliases
        and current.location is plan.location
        and current.health is HealthStatus.HEALTHY
        and current.enabled
        and current.priority == configuration.priority
        and current.capabilities == configuration.capabilities
        and current.adapter_metadata == configuration.adapter_metadata
    )
