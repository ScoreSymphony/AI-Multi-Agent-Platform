"""Platform-owned model routing and invocation service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    DataClassification,
    EgressCostClass,
    EgressProfile,
    EgressProfileTrust,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    JsonValue,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSelection,
    ModelStreamEvent,
    digest_egress_payload,
)
from ai_multi_agent_platform.security.egress import EgressGate

from .protocol import CanonicalModelRequest, CanonicalModelResponse
from .registry import ModelRegistry
from .router import DeterministicModelRouter
from .types import ModelConfiguration, ModelLocation


class ModelRuntime:
    """Route and invoke a model without exposing provider-native identities."""

    def __init__(
        self,
        registry: ModelRegistry,
        router: DeterministicModelRouter | None = None,
        *,
        egress_gate: EgressGate | None = None,
    ) -> None:
        self.registry = registry
        # A missing caller override must never disable enforcement.
        self.egress_gate = egress_gate or EgressGate()
        self.router = router or DeterministicModelRouter(
            registry,
            candidate_policy_hook=self._candidate_egress_policy,
        )

    async def select(self, request: ModelRequest) -> ModelSelection:
        return await self.router.select_provider(request)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        config, provider, routed_request = await self._resolve_target(request)
        try:
            provider_response = await provider.generate(routed_request)
        except asyncio.CancelledError as exc:
            raise self._cancelled_error(request, config) from exc
        return self._normalize_response(request, config, provider_response)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Route once and expose canonical incremental provider events."""

        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            config, provider, routed_request = await self._resolve_target(request)
            try:
                async for event in provider.stream(routed_request):
                    if event.request_id != request.request_id:
                        raise ContractError(
                            ErrorCode.CONTRACT_VIOLATION,
                            "model provider stream event request_id does not match request",
                            provider_id=config.provider_id,
                            details={
                                "expected_request_id": request.request_id,
                                "reported_request_id": event.request_id,
                            },
                        )

                    provider_reported_model_ref = event.model_ref
                    response = event.response
                    if response is not None:
                        response = self._normalize_response(request, config, response)

                    runtime_metadata = self._runtime_metadata(
                        request,
                        config,
                        provider_reported_model_ref,
                    )
                    yield replace(
                        event,
                        model_ref=config.config_id,
                        response=response,
                        adapter_metadata=event.adapter_metadata + (runtime_metadata,),
                    )
            except asyncio.CancelledError as exc:
                raise self._cancelled_error(request, config) from exc

        return iterate()

    async def generate_canonical(
        self,
        request: CanonicalModelRequest,
    ) -> CanonicalModelResponse:
        """Execute the rich issue-#10 request shape through the stable provider seam."""

        response = await self.generate(request.to_contract_request())
        return CanonicalModelResponse.from_contract_response(response)

    def stream_canonical(
        self,
        request: CanonicalModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream a rich request through the same canonical routing/runtime seam."""

        return self.stream(request.to_contract_request())

    async def _candidate_egress_policy(
        self,
        request: ModelRequest,
        config: ModelConfiguration,
    ) -> tuple[bool, str | None]:
        """Exclude disclosure-incompatible candidates before deterministic selection."""

        try:
            egress_request = _model_egress_request(request, config)
        except ContractError as exc:
            if exc.code is ErrorCode.INVALID_CONFIGURATION:
                return False, "invalid_egress_profile"
            raise
        decision = await self.egress_gate.evaluate(egress_request)
        return decision.allowed, None if decision.allowed else decision.reason_code.value

    async def _resolve_target(
        self,
        request: ModelRequest,
    ) -> tuple[ModelConfiguration, ModelProvider, ModelRequest]:
        selection = await self.select(request)
        if selection.model_ref is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model router returned no canonical model configuration ID",
                provider_id=selection.provider_id,
            )

        config = self.registry.get_model(selection.model_ref)
        if config.provider_id != selection.provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "router provider selection does not match model registry configuration",
                provider_id=selection.provider_id,
                details={
                    "model_config_id": config.config_id,
                    "registry_provider_id": config.provider_id,
                },
            )

        provider = self.registry.get_provider(selection.provider_id)
        # Defense in depth for custom routers and any future route implementation.
        await self.egress_gate.enforce(_model_egress_request(request, config))

        requirements = dict(request.requirements)
        requirements["model_config_id"] = config.config_id
        routed_request = replace(request, requirements=requirements)
        return config, provider, routed_request

    def _normalize_response(
        self,
        request: ModelRequest,
        config: ModelConfiguration,
        provider_response: ModelResponse,
    ) -> ModelResponse:
        if provider_response.request_id != request.request_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model provider response request_id does not match request",
                provider_id=config.provider_id,
                details={
                    "expected_request_id": request.request_id,
                    "reported_request_id": provider_response.request_id,
                },
            )
        runtime_metadata = self._runtime_metadata(
            request,
            config,
            provider_response.model_ref,
        )
        return replace(
            provider_response,
            model_ref=config.config_id,
            adapter_metadata=provider_response.adapter_metadata + (runtime_metadata,),
        )

    def _runtime_metadata(
        self,
        request: ModelRequest,
        config: ModelConfiguration,
        provider_reported_model_ref: str,
    ) -> AdapterMetadata:
        return AdapterMetadata(
            namespace="platform-model-runtime",
            values={
                "model_config_id": config.config_id,
                "provider_id": config.provider_id,
                "provider_reported_model_ref": provider_reported_model_ref,
                "correlation_id": request.context.correlation_id,
            },
        )

    @staticmethod
    def _cancelled_error(
        request: ModelRequest,
        config: ModelConfiguration,
    ) -> ContractError:
        return ContractError(
            ErrorCode.CANCELLED,
            "model request was cancelled",
            provider_id=config.provider_id,
            details={
                "request_id": request.request_id,
                "model_config_id": config.config_id,
            },
        )


def _model_egress_request(request: ModelRequest, config: ModelConfiguration) -> EgressRequest:
    classification = _request_classification(request)
    return EgressRequest(
        request_id=f"model:{request.request_id}:{config.config_id}",
        target=_model_egress_target(config),
        context=request.context,
        classification=classification,
        resource_type="model_request",
        payload_digest=digest_egress_payload({"messages": list(request.messages)}),
        task_id=_optional_request_ref(request, "task_id"),
        run_id=_optional_request_ref(request, "run_id"),
        policy_descriptors={
            "model_config_id": config.config_id,
            "model_location": config.location.value,
        },
    )


def _model_egress_target(config: ModelConfiguration) -> EgressTarget:
    profile = _model_egress_profile(config)
    if profile is not None:
        return EgressTarget(
            kind=EgressTargetKind.MODEL_PROVIDER,
            target_id=config.config_id,
            profile=profile,
            policy_metadata={"provider_id": config.provider_id},
        )
    return EgressTarget(
        kind=EgressTargetKind.MODEL_PROVIDER,
        target_id=config.config_id,
        posture=_posture_for_model(config.location),
        allowed_classifications=_allowed_classifications(config),
        policy_metadata={
            "allow_sensitive_external": _allow_sensitive_external(config),
            "provider_id": config.provider_id,
        },
    )


def _model_egress_profile(config: ModelConfiguration) -> EgressProfile | None:
    raw = config.resource_hints.get("egress_profile")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _profile_error(config, "egress_profile must be an object")

    profile_id = _profile_string(
        raw,
        "profile_id",
        config,
        default=f"egress-profile:model:{config.config_id}",
    )
    revision = _profile_positive_int(raw, "revision", config, default=config.revision)
    posture = _profile_enum(
        raw,
        "posture",
        EgressTargetPosture,
        config,
        default=_posture_for_model(config.location),
    )
    cost_class = _profile_enum(
        raw,
        "cost_class",
        EgressCostClass,
        config,
        default=EgressCostClass.UNKNOWN,
    )
    trust = _profile_enum(
        raw,
        "trust",
        EgressProfileTrust,
        config,
        default=EgressProfileTrust.UNVERIFIED,
    )
    allowed = _profile_classifications(
        raw,
        "allowed_classifications",
        config,
        default=_allowed_classifications(config),
    )
    denied = _profile_classifications(raw, "denied_classifications", config, default=())
    metadata = _profile_mapping(raw, "metadata", config)
    if "allow_sensitive_external" not in metadata:
        metadata["allow_sensitive_external"] = _allow_sensitive_external(config)

    return EgressProfile(
        profile_id=profile_id,
        revision=revision,
        target_kind=EgressTargetKind.MODEL_PROVIDER,
        target_id=config.config_id,
        posture=posture,
        allowed_classifications=allowed,
        denied_classifications=denied,
        network_egress_required=_profile_optional_bool(
            raw,
            "network_egress_required",
            config,
            default=(posture is EgressTargetPosture.EXTERNAL),
        ),
        data_retention_policy=_profile_optional_string(raw, "data_retention_policy", config),
        training_policy=_profile_optional_string(raw, "training_policy", config),
        logging_policy=_profile_optional_string(raw, "logging_policy", config),
        jurisdiction=_profile_optional_string(raw, "jurisdiction", config),
        cost_class=cost_class,
        credential_required=_profile_optional_bool(
            raw,
            "credential_required",
            config,
            default=None,
        ),
        policy_source=_profile_string(
            raw,
            "policy_source",
            config,
            default="model-resource-hints",
        ),
        source_revision=_profile_string(
            raw,
            "source_revision",
            config,
            default=str(config.revision),
        ),
        trust=trust,
        metadata=metadata,
    )


def _request_classification(request: ModelRequest) -> DataClassification:
    """Read platform-owned routing metadata; model/prompt text is never classification authority."""

    raw = request.requirements.get("data_classification", DataClassification.INTERNAL.value)
    if not isinstance(raw, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "data_classification must be a string")
    try:
        return DataClassification(raw)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unknown data_classification {raw!r}",
        ) from exc


def _posture_for_model(location: ModelLocation) -> EgressTargetPosture:
    if location is ModelLocation.LOCAL:
        return EgressTargetPosture.LOCAL
    if location is ModelLocation.SELF_HOSTED:
        return EgressTargetPosture.INTERNAL
    return EgressTargetPosture.EXTERNAL


def _allowed_classifications(config: ModelConfiguration) -> tuple[DataClassification, ...]:
    raw = config.resource_hints.get("allowed_data_classifications")
    if raw is None:
        return ()
    return _parse_classification_list(raw, "allowed_data_classifications", config)


def _allow_sensitive_external(config: ModelConfiguration) -> bool:
    raw = config.resource_hints.get("allow_sensitive_external", False)
    if not isinstance(raw, bool):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "allow_sensitive_external must be a boolean",
            provider_id=config.provider_id,
        )
    return raw


def _optional_request_ref(request: ModelRequest, key: str) -> str | None:
    raw = request.requirements.get(key)
    if raw is None:
        return None
    return raw if isinstance(raw, str) and raw.strip() else None


def _profile_string(
    raw: Mapping[str, JsonValue],
    key: str,
    config: ModelConfiguration,
    *,
    default: str,
) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise _profile_error(config, f"egress_profile.{key} must be a non-blank string")
    return value


def _profile_optional_string(
    raw: Mapping[str, JsonValue],
    key: str,
    config: ModelConfiguration,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _profile_error(config, f"egress_profile.{key} must be a non-blank string")
    return value


def _profile_positive_int(
    raw: Mapping[str, JsonValue],
    key: str,
    config: ModelConfiguration,
    *,
    default: int,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _profile_error(config, f"egress_profile.{key} must be a positive integer")
    return value


def _profile_optional_bool(
    raw: Mapping[str, JsonValue],
    key: str,
    config: ModelConfiguration,
    *,
    default: bool | None,
) -> bool | None:
    value = raw.get(key, default)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _profile_error(config, f"egress_profile.{key} must be a boolean")
    return value


def _profile_enum[T: str](
    raw: Mapping[str, JsonValue],
    key: str,
    enum_type: type[T],
    config: ModelConfiguration,
    *,
    default: T,
) -> T:
    value = raw.get(key, default)
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise _profile_error(config, f"egress_profile.{key} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _profile_error(config, f"egress_profile.{key} contains an unknown value") from exc


def _profile_classifications(
    raw: Mapping[str, JsonValue],
    key: str,
    config: ModelConfiguration,
    *,
    default: tuple[DataClassification, ...],
) -> tuple[DataClassification, ...]:
    value = raw.get(key)
    if value is None:
        return default
    return _parse_classification_list(value, f"egress_profile.{key}", config)


def _parse_classification_list(
    value: JsonValue,
    field_name: str,
    config: ModelConfiguration,
) -> tuple[DataClassification, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _profile_error(config, f"{field_name} must be a list of classification strings")
    try:
        return tuple(DataClassification(item) for item in value if isinstance(item, str))
    except ValueError as exc:
        raise _profile_error(config, f"{field_name} contains an unknown classification") from exc


def _profile_mapping(
    raw: Mapping[str, JsonValue],
    key: str,
    config: ModelConfiguration,
) -> dict[str, JsonValue]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise _profile_error(config, f"egress_profile.{key} must be an object")
    return dict(value)


def _profile_error(config: ModelConfiguration, message: str) -> ContractError:
    return ContractError(
        ErrorCode.INVALID_CONFIGURATION,
        message,
        provider_id=config.provider_id,
        details={"model_config_id": config.config_id},
    )
