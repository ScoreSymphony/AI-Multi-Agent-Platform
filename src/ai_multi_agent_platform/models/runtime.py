"""Platform-owned model routing and invocation service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
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
        self.router = router or DeterministicModelRouter(registry)
        # A missing caller override must never disable enforcement.
        self.egress_gate = egress_gate or EgressGate()

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
        classification = _request_classification(request)
        await self.egress_gate.enforce(
            EgressRequest(
                request_id=f"model:{request.request_id}:{config.config_id}",
                target=EgressTarget(
                    kind=EgressTargetKind.MODEL_PROVIDER,
                    target_id=config.config_id,
                    posture=_posture_for_model(config.location),
                    allowed_classifications=_allowed_classifications(config),
                    policy_metadata={
                        "allow_sensitive_external": _allow_sensitive_external(config),
                        "provider_id": config.provider_id,
                    },
                ),
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
        )

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
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "allowed_data_classifications must be a list of classification strings",
            provider_id=config.provider_id,
        )
    try:
        return tuple(DataClassification(item) for item in raw if isinstance(item, str))
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "allowed_data_classifications contains an unknown classification",
            provider_id=config.provider_id,
        ) from exc


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
