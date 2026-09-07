from __future__ import annotations

import asyncio

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
)
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
    ModelRuntime,
)
from ai_multi_agent_platform.observability import (
    AccountingBridgeExporter,
    InMemoryExporter,
    ObservedModelProvider,
    Telemetry,
)
from ai_multi_agent_platform.testing import FakeModelProvider


class UsageReportingProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="usage-reporting-provider",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            request_id=request.request_id,
            text="ok",
            model_ref="provider-native/opaque-model-name",
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        )


def _configuration(config_id: str, *, priority: int) -> ModelConfiguration:
    return ModelConfiguration(
        config_id=config_id,
        display_name=config_id,
        provider_id="usage-reporting-provider",
        capabilities=ModelCapabilities(
            context_window=32_768,
            streaming=True,
            modalities=("text",),
        ),
        health=HealthStatus.HEALTHY,
        priority=priority,
    )


def test_auto_routed_model_usage_is_attributed_to_selected_canonical_configuration() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    exporter = InMemoryExporter()
    telemetry = Telemetry(AccountingBridgeExporter(exporter, accounting))
    provider = UsageReportingProvider()

    registry = ModelRegistry()
    registry.register_provider(ObservedModelProvider(provider, telemetry))
    registry.register_model(_configuration("model-low-priority", priority=10))
    registry.register_model(_configuration("model-auto-selected", priority=100))
    runtime = ModelRuntime(registry)

    response = asyncio.run(
        runtime.generate(
            ModelRequest(
                request_id="model-call-auto-routing",
                messages=("hello",),
                context=OperationContext(
                    correlation_id="correlation-model-auto-routing",
                    owner_type="user",
                    owner_id="alice",
                ),
            )
        )
    )

    assert response.model_ref == "model-auto-selected"
    assert len(provider.calls) == 1
    assert provider.calls[0].requirements["model_config_id"] == "model-auto-selected"

    by_metric = {record.metric_type: record for record in accounting.query()}
    for metric_type in (
        "model.call.count",
        "model.call.duration",
        "model.tokens.input",
        "model.tokens.output",
        "model.tokens.total",
    ):
        record = by_metric[metric_type]
        assert record.scope.model_config_id == "model-auto-selected"
        assert record.scope.model_provider_id == "usage-reporting-provider"
        assert record.provider == "usage-reporting-provider"
        assert record.scope.model_config_id != "provider-native/opaque-model-name"

    assert by_metric["model.tokens.input"].quantity == 11.0
    assert by_metric["model.tokens.output"].quantity == 7.0
    assert by_metric["model.tokens.total"].quantity == 18.0
