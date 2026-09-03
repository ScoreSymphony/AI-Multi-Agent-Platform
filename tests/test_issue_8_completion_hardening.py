from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_CONFIGURATION_SCHEMA,
    HERMES_PINNED_REVISION,
    HermesAdapterConfig,
    HermesBridgeMode,
    HermesCompatibilityStatus,
    HermesDiagnosticsMode,
    HermesHttpResponse,
    HermesOrchestrator,
    HermesRetryBehavior,
    HermesRuntimeMode,
)
from ai_multi_agent_platform.configuration import (
    ConfigLayer,
    ConfigScope,
    ConfigSource,
    ConfigurationResolver,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
    OperationControl,
    PlanRequest,
)
from ai_multi_agent_platform.domain import new_id


@dataclass(frozen=True, slots=True)
class RecordedCall:
    method: str
    url: str
    timeout_seconds: float


class SlowHermesTransport:
    def __init__(
        self,
        *,
        admission_delay: float = 0.0,
        status_delay: float = 0.0,
        stop_delay: float = 0.0,
    ) -> None:
        self.admission_delay = admission_delay
        self.status_delay = status_delay
        self.stop_delay = stop_delay
        self.calls: list[RecordedCall] = []
        self.stop_seen = asyncio.Event()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        del payload, headers
        self.calls.append(RecordedCall(method, url, timeout_seconds))
        if method == "POST" and url.endswith("/v1/runs"):
            await asyncio.sleep(self.admission_delay)
            return HermesHttpResponse(202, {"run_id": "run_timeout", "status": "started"})
        if method == "GET" and url.endswith("/v1/runs/run_timeout"):
            await asyncio.sleep(self.status_delay)
            return HermesHttpResponse(200, {"run_id": "run_timeout", "status": "running"})
        if method == "POST" and url.endswith("/v1/runs/run_timeout/stop"):
            await asyncio.sleep(self.stop_delay)
            self.stop_seen.set()
            return HermesHttpResponse(200, {"ok": True})
        raise AssertionError(f"unexpected Hermes request: {method} {url}")


def _request(timeout_seconds: float) -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective="Verify the provider-boundary timeout",
        context=OperationContext(
            correlation_id="corr-hermes-timeout",
            control=OperationControl(
                timeout_seconds=timeout_seconds,
                idempotency_key="hermes-timeout-test",
            ),
        ),
    )


def _context(timeout_seconds: float) -> OperationContext:
    return OperationContext(
        correlation_id="corr-hermes-run-control",
        control=OperationControl(timeout_seconds=timeout_seconds),
    )


def test_committed_hermes_example_uses_platform_configuration_schema() -> None:
    example_path = Path(__file__).parents[1] / "config" / "hermes.example.json"
    raw = json.loads(example_path.read_text())
    effective = ConfigurationResolver(HERMES_CONFIGURATION_SCHEMA).resolve(
        (
            ConfigLayer(
                ConfigScope.ADAPTER,
                raw,
                ConfigSource("issue-8-hermes-example", "json"),
            ),
        )
    )
    config = HermesAdapterConfig.from_mapping(effective.values)

    assert config.enabled is False
    assert config.runtime_mode is HermesRuntimeMode.API_SERVER
    assert config.retry_behavior is HermesRetryBehavior.PLATFORM_OWNED
    assert config.bridge_mode is HermesBridgeMode.STRICT
    assert config.diagnostics_mode is HermesDiagnosticsMode.PLATFORM_ONLY
    assert config.compatibility_status is HermesCompatibilityStatus.VERIFIED_PIN
    assert config.pinned_revision == HERMES_PINNED_REVISION
    assert config.model_bridge["model-local-example"] == "openai-compatible/local-model"
    assert config.capability_bridge["tool.echo"] == "echo"


def test_hermes_configuration_is_strict_and_compatibility_status_is_truthful() -> None:
    with pytest.raises(ValueError, match="unknown Hermes configuration fields"):
        HermesAdapterConfig.from_mapping({"enabled": False, "silent_bypass": True})

    with pytest.raises(ValueError, match="verified_pin"):
        HermesAdapterConfig(
            pinned_revision="different-revision",
            compatibility_status=HermesCompatibilityStatus.VERIFIED_PIN,
        )

    unverified = HermesAdapterConfig(
        pinned_revision="different-revision",
        compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
    )
    assert unverified.compatibility_status is HermesCompatibilityStatus.UNVERIFIED_PIN

    with pytest.raises(ValueError, match="duplicate canonical_id"):
        HermesAdapterConfig.from_mapping(
            {
                "capability_bridge": [
                    {"canonical_id": "tool.echo", "hermes_target": "echo"},
                    {"canonical_id": "tool.echo", "hermes_target": "echo-again"},
                ]
            }
        )


def test_descriptor_advertises_explicit_runtime_retry_bridge_and_compatibility_modes() -> None:
    descriptor = HermesOrchestrator(HermesAdapterConfig(enabled=True)).descriptor
    metadata = descriptor.adapter_metadata[0].values

    assert metadata["transport"] == "api_server"
    assert metadata["retry_behavior"] == "platform_owned"
    assert metadata["bridge_mode"] == "strict"
    assert metadata["diagnostics_mode"] == "platform_only"
    assert metadata["compatibility_status"] == "verified_pin"
    assert set(descriptor.supported_operations) == {"plan", "cancel", "reconcile"}


def test_hermes_adapter_source_has_no_hermes_runtime_import() -> None:
    adapter_path = (
        Path(__file__).parents[1] / "src" / "ai_multi_agent_platform" / "adapters" / "hermes.py"
    )
    tree = ast.parse(adapter_path.read_text())
    imported_roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.extend(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.append(node.module.split(".", maxsplit=1)[0])

    assert not any(name.startswith("hermes") for name in imported_roots)


def test_canonical_timeout_bounds_hermes_run_admission() -> None:
    async def scenario() -> None:
        transport = SlowHermesTransport(admission_delay=1.0)
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, request_timeout_seconds=5.0),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        started = monotonic()
        with pytest.raises(ContractError) as captured:
            await orchestrator.plan(_request(0.02))
        elapsed = monotonic() - started

        assert captured.value.code is ErrorCode.TIMEOUT
        assert elapsed < 0.25
        assert transport.calls[0].timeout_seconds <= 0.02

    asyncio.run(scenario())


def test_timeout_after_admission_schedules_best_effort_stop_without_extending_boundary() -> None:
    async def scenario() -> None:
        transport = SlowHermesTransport(status_delay=1.0)
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, request_timeout_seconds=5.0),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        started = monotonic()
        with pytest.raises(ContractError) as captured:
            await orchestrator.plan(_request(0.03))
        elapsed = monotonic() - started

        assert captured.value.code is ErrorCode.TIMEOUT
        assert elapsed < 0.25
        await asyncio.wait_for(transport.stop_seen.wait(), timeout=0.25)
        status_call = next(call for call in transport.calls if call.method == "GET")
        assert status_call.timeout_seconds <= 0.03

    asyncio.run(scenario())


def test_reconcile_honors_canonical_provider_boundary_timeout() -> None:
    async def scenario() -> None:
        transport = SlowHermesTransport(status_delay=1.0)
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, request_timeout_seconds=5.0),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        started = monotonic()
        with pytest.raises(ContractError) as captured:
            await orchestrator.reconcile_external_run("run_timeout", _context(0.02))
        elapsed = monotonic() - started

        assert captured.value.code is ErrorCode.TIMEOUT
        assert elapsed < 0.25
        assert transport.calls[0].timeout_seconds <= 0.02

    asyncio.run(scenario())


def test_cancel_honors_canonical_provider_boundary_timeout() -> None:
    async def scenario() -> None:
        transport = SlowHermesTransport(stop_delay=1.0)
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, request_timeout_seconds=5.0),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        started = monotonic()
        with pytest.raises(ContractError) as captured:
            await orchestrator.cancel_external_run("run_timeout", _context(0.02))
        elapsed = monotonic() - started

        assert captured.value.code is ErrorCode.TIMEOUT
        assert elapsed < 0.25
        assert transport.calls[0].timeout_seconds <= 0.02

    asyncio.run(scenario())


def test_retryable_http_failures_remain_canonical_instead_of_hidden_retries() -> None:
    class ErrorTransport:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.calls = 0

        async def request_json(
            self,
            method: str,
            url: str,
            *,
            payload: Mapping[str, JsonValue] | None,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> HermesHttpResponse:
            del method, url, payload, headers, timeout_seconds
            self.calls += 1
            return HermesHttpResponse(self.status_code, {"detail": "temporary"})

    async def scenario(status_code: int, code: ErrorCode) -> None:
        transport = ErrorTransport(status_code)
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as captured:
            await orchestrator.plan(_request(0.5))
        assert captured.value.code is code
        assert captured.value.retryable is True
        assert transport.calls == 1

    asyncio.run(scenario(429, ErrorCode.RATE_LIMITED))
    asyncio.run(scenario(503, ErrorCode.UNAVAILABLE))
