"""Deterministic transport backpressure, outage and duplicate-delivery benchmarks."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.messaging import (
    IdempotentConsumer,
    InProcessMessageTransport,
    MessageKind,
    Subscription,
    TransportEnvelope,
)

from .models import LatencyDistribution
from .single_node import _environment_metadata, _open_file_descriptor_count, _peak_rss_bytes

TRANSPORT_FAULT_REPORT_SCHEMA_VERSION = "1.0"
_TRANSPORT_TOPIC = "benchmark.transport"
_TRANSPORT_GROUP = "benchmark-workers"


@dataclass(frozen=True, slots=True)
class TransportFaultBenchmarkSpec:
    """One bounded deterministic transport fault-under-load scenario."""

    benchmark_id: str
    benchmark_version: str
    scenario: str
    transport_profile: str
    batch_size: int
    concurrency: int
    max_queue_size: int
    fault_operations: int
    timeout_seconds: float
    expected_invariants: tuple[str, ...] = (
        "accepted-messages-are-not-silently-lost",
        "expected-faults-use-canonical-contract-errors",
        "recovery-restores-successful-publish-and-delivery",
        "duplicate-delivery-never-implies-exactly-once-transport",
    )
    captured_metrics: tuple[str, ...] = (
        "publish-latency-p50-p95-p99",
        "delivery-latency-p50-p95-p99",
        "recovery-latency",
        "expected-and-unexpected-error-counts",
        "redelivery-and-idempotent-handler-counts",
        "cpu-memory-open-files",
        "correctness",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.scenario not in {"backpressure", "outage", "duplicate-delivery"}:
            raise ValueError("unsupported transport fault scenario")
        if self.transport_profile != "in-process-reference":
            raise ValueError("transport fault benchmark requires in-process-reference transport")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        if self.fault_operations < 0:
            raise ValueError("fault_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.scenario == "backpressure":
            if self.batch_size != self.max_queue_size:
                raise ValueError("backpressure batch_size must equal max_queue_size")
            if self.fault_operations != 1:
                raise ValueError("backpressure requires exactly one overflow fault operation")
        elif self.scenario == "outage":
            if self.fault_operations < 1:
                raise ValueError("outage requires at least one fault operation")
            if self.max_queue_size < self.batch_size * 2:
                raise ValueError("outage queue must hold both accepted load phases")
        else:
            if self.fault_operations != 0:
                raise ValueError("duplicate-delivery does not use fault_operations")
            if self.max_queue_size < self.batch_size + 1:
                raise ValueError("duplicate-delivery queue must hold the duplicate publish")


@dataclass(frozen=True, slots=True)
class TransportFaultResourceMetrics:
    process_cpu_seconds: float
    traced_memory_current_bytes: int
    traced_memory_peak_bytes: int
    peak_rss_bytes: int | None
    open_file_descriptors: int | None


@dataclass(frozen=True, slots=True)
class TransportFaultCorrectnessSummary:
    attempted_publishes: int
    accepted_publishes: int
    expected_failures: int
    unexpected_failures: int
    delivery_attempts: int
    unique_delivered_message_ids: int
    handler_executions: int
    suppressed_duplicate_handlers: int
    redelivered_deliveries: int
    expected_duplicate_delivery_attempts: int
    observed_duplicate_delivery_attempts: int
    message_loss_count: int
    recovered: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class TransportFaultBenchmarkReport:
    schema_version: str
    benchmark: TransportFaultBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    throughput_attempts_per_second: float
    publish_latency: LatencyDistribution
    delivery_latency: LatencyDistribution
    recovery_latency: LatencyDistribution
    resources: TransportFaultResourceMetrics
    correctness: TransportFaultCorrectnessSummary
    error_counts: Mapping[str, int]
    measurements: Mapping[str, int | float | str | bool | None]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["error_counts"] = dict(self.error_counts)
        payload["measurements"] = dict(self.measurements)
        payload["errors"] = list(self.errors)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            for key in ("expected_invariants", "captured_metrics"):
                benchmark[key] = list(benchmark[key])
        return payload


@dataclass(slots=True)
class _ScenarioEvidence:
    attempted_publishes: int = 0
    accepted_ids: list[str] = field(default_factory=list)
    expected_failures: int = 0
    unexpected_failures: int = 0
    delivery_ids: list[str] = field(default_factory=list)
    handler_ids: list[str] = field(default_factory=list)
    suppressed_duplicate_handlers: int = 0
    redelivered_deliveries: int = 0
    expected_duplicate_delivery_attempts: int = 0
    publish_latency: list[float] = field(default_factory=list)
    delivery_latency: list[float] = field(default_factory=list)
    recovery_latency: list[float] = field(default_factory=list)
    error_counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    recovered: bool = False


class TransportFaultBenchmarkHarness:
    """Exercise bounded reference-transport fault seams without private state mutation."""

    def __init__(self, *, platform_commit: str = "unknown") -> None:
        self._platform_commit = platform_commit

    async def run(self, spec: TransportFaultBenchmarkSpec) -> TransportFaultBenchmarkReport:
        transport = InProcessMessageTransport(
            max_queue_size=spec.max_queue_size,
            provider_id="benchmark-in-process",
        )
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        started_at = datetime.now(UTC).isoformat()
        measurement_started = time.perf_counter()
        evidence = _ScenarioEvidence()
        try:
            if spec.scenario == "backpressure":
                await self._run_backpressure(transport, spec, evidence)
            elif spec.scenario == "outage":
                await self._run_outage(transport, spec, evidence)
            else:
                await self._run_duplicate_delivery(transport, spec, evidence)
        finally:
            await transport.close(graceful=True)
        duration = time.perf_counter() - measurement_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()

        accepted_unique = set(evidence.accepted_ids)
        delivered_unique = set(evidence.delivery_ids)
        message_loss = len(accepted_unique - delivered_unique)
        observed_duplicate_attempts = len(evidence.delivery_ids) - len(delivered_unique)
        expected_error_total = evidence.expected_failures
        actual_error_total = sum(evidence.error_counts.values())
        passed = (
            evidence.unexpected_failures == 0
            and actual_error_total == expected_error_total
            and message_loss == 0
            and observed_duplicate_attempts == evidence.expected_duplicate_delivery_attempts
            and evidence.recovered
        )
        if spec.scenario == "duplicate-delivery":
            passed = (
                passed
                and len(evidence.handler_ids) == spec.batch_size
                and len(set(evidence.handler_ids)) == spec.batch_size
                and evidence.suppressed_duplicate_handlers == 1
                and evidence.redelivered_deliveries == 1
            )
        else:
            passed = passed and not evidence.handler_ids

        correctness = TransportFaultCorrectnessSummary(
            attempted_publishes=evidence.attempted_publishes,
            accepted_publishes=len(evidence.accepted_ids),
            expected_failures=evidence.expected_failures,
            unexpected_failures=evidence.unexpected_failures,
            delivery_attempts=len(evidence.delivery_ids),
            unique_delivered_message_ids=len(delivered_unique),
            handler_executions=len(evidence.handler_ids),
            suppressed_duplicate_handlers=evidence.suppressed_duplicate_handlers,
            redelivered_deliveries=evidence.redelivered_deliveries,
            expected_duplicate_delivery_attempts=evidence.expected_duplicate_delivery_attempts,
            observed_duplicate_delivery_attempts=observed_duplicate_attempts,
            message_loss_count=message_loss,
            recovered=evidence.recovered,
            passed=passed,
        )
        throughput = evidence.attempted_publishes / duration if duration > 0 else 0.0
        return TransportFaultBenchmarkReport(
            schema_version=TRANSPORT_FAULT_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_attempts_per_second=round(throughput, 6),
            publish_latency=LatencyDistribution.from_seconds(evidence.publish_latency),
            delivery_latency=LatencyDistribution.from_seconds(evidence.delivery_latency),
            recovery_latency=LatencyDistribution.from_seconds(evidence.recovery_latency),
            resources=TransportFaultResourceMetrics(
                process_cpu_seconds=round(process_cpu_seconds, 6),
                traced_memory_current_bytes=traced_current,
                traced_memory_peak_bytes=traced_peak,
                peak_rss_bytes=_peak_rss_bytes(),
                open_file_descriptors=_open_file_descriptor_count(),
            ),
            correctness=correctness,
            error_counts=dict(sorted(evidence.error_counts.items())),
            measurements={
                "accepted_unique_message_ids": len(accepted_unique),
                "delivered_unique_message_ids": len(delivered_unique),
                "transport_available_after_recovery": transport.descriptor.available,
                "transport_health_after_recovery": transport.descriptor.health.value,
                "max_queue_size": spec.max_queue_size,
            },
            errors=tuple(evidence.errors),
        )

    async def _run_backpressure(
        self,
        transport: InProcessMessageTransport,
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
    ) -> None:
        initial = [self._envelope(index) for index in range(spec.batch_size)]
        await self._publish_batch(transport, initial, spec, evidence)
        overflow = self._envelope(spec.batch_size)
        await self._expect_publish_error(
            transport,
            overflow,
            spec,
            evidence,
            ErrorCode.RESOURCE_EXHAUSTED,
        )

        stream = transport.subscribe(Subscription(_TRANSPORT_TOPIC, "drain-a", _TRANSPORT_GROUP))
        first = await self._next_delivery(stream, spec, evidence)
        await transport.ack(first)

        recovery = self._envelope(spec.batch_size + 1)
        started = time.perf_counter()
        accepted = await self._publish_one(transport, recovery, spec, evidence)
        evidence.recovery_latency.append(time.perf_counter() - started)
        if not accepted:
            evidence.errors.append("recovery publish failed after one queue slot was drained")

        for _ in range(spec.batch_size):
            delivery = await self._next_delivery(stream, spec, evidence)
            await transport.ack(delivery)
        await stream.aclose()
        evidence.recovered = accepted and transport.descriptor.available

    async def _run_outage(
        self,
        transport: InProcessMessageTransport,
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
    ) -> None:
        pre = [self._envelope(index) for index in range(spec.batch_size)]
        await self._publish_batch(transport, pre, spec, evidence)
        await transport.set_available(False)
        outage = [self._envelope(spec.batch_size + index) for index in range(spec.fault_operations)]
        await asyncio.gather(
            *(
                self._expect_publish_error(
                    transport,
                    envelope,
                    spec,
                    evidence,
                    ErrorCode.UNAVAILABLE,
                )
                for envelope in outage
            )
        )
        recovery_started = time.perf_counter()
        await transport.set_available(True)
        post = [
            self._envelope(spec.batch_size + spec.fault_operations + index)
            for index in range(spec.batch_size)
        ]
        await self._publish_batch(transport, post, spec, evidence)
        evidence.recovery_latency.append(time.perf_counter() - recovery_started)

        stream = transport.subscribe(Subscription(_TRANSPORT_TOPIC, "drain-b", _TRANSPORT_GROUP))
        for _ in range(spec.batch_size * 2):
            delivery = await self._next_delivery(stream, spec, evidence)
            await transport.ack(delivery)
        await stream.aclose()
        evidence.recovered = transport.descriptor.available

    async def _run_duplicate_delivery(
        self,
        transport: InProcessMessageTransport,
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
    ) -> None:
        envelopes = [self._envelope(index) for index in range(spec.batch_size)]
        await self._publish_one(transport, envelopes[0], spec, evidence)
        if len(envelopes) > 1:
            await self._publish_batch(transport, envelopes[1:], spec, evidence)
        await self._publish_one(transport, envelopes[0], spec, evidence)

        first_stream = transport.subscribe(
            Subscription(_TRANSPORT_TOPIC, "consumer-before-restart", _TRANSPORT_GROUP)
        )
        first = await self._next_delivery(first_stream, spec, evidence)
        await first_stream.aclose()

        stream = transport.subscribe(
            Subscription(_TRANSPORT_TOPIC, "consumer-after-restart", _TRANSPORT_GROUP)
        )
        consumer = IdempotentConsumer(transport)

        async def handler(envelope: TransportEnvelope) -> None:
            evidence.handler_ids.append(envelope.message_id)

        for _ in range(spec.batch_size + 1):
            delivery = await self._next_delivery(stream, spec, evidence)
            if delivery.metadata.redelivered:
                evidence.redelivered_deliveries += 1
            handled = await consumer.handle(delivery, handler)
            if not handled:
                evidence.suppressed_duplicate_handlers += 1
        await stream.aclose()
        evidence.expected_duplicate_delivery_attempts = 2
        evidence.recovered = transport.descriptor.available

    async def _publish_batch(
        self,
        transport: InProcessMessageTransport,
        envelopes: list[TransportEnvelope],
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
    ) -> None:
        semaphore = asyncio.Semaphore(spec.concurrency)

        async def publish(envelope: TransportEnvelope) -> None:
            async with semaphore:
                await self._publish_one(transport, envelope, spec, evidence)

        await asyncio.gather(*(publish(envelope) for envelope in envelopes))

    async def _publish_one(
        self,
        transport: InProcessMessageTransport,
        envelope: TransportEnvelope,
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
    ) -> bool:
        evidence.attempted_publishes += 1
        started = time.perf_counter()
        try:
            await asyncio.wait_for(
                transport.publish(_TRANSPORT_TOPIC, envelope),
                timeout=spec.timeout_seconds,
            )
        except Exception as exc:
            evidence.unexpected_failures += 1
            evidence.errors.append(
                f"unexpected publish failure for {envelope.message_id}: {type(exc).__name__}: {exc}"
            )
            return False
        finally:
            evidence.publish_latency.append(time.perf_counter() - started)
        evidence.accepted_ids.append(envelope.message_id)
        return True

    async def _expect_publish_error(
        self,
        transport: InProcessMessageTransport,
        envelope: TransportEnvelope,
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
        expected_code: ErrorCode,
    ) -> None:
        evidence.attempted_publishes += 1
        started = time.perf_counter()
        try:
            await asyncio.wait_for(
                transport.publish(_TRANSPORT_TOPIC, envelope),
                timeout=spec.timeout_seconds,
            )
        except ContractError as exc:
            evidence.error_counts[exc.code.value] += 1
            if exc.code is expected_code and exc.retryable:
                evidence.expected_failures += 1
            else:
                evidence.unexpected_failures += 1
                evidence.errors.append(
                    f"expected retryable {expected_code.value}, got {exc.code.value} "
                    f"retryable={exc.retryable}"
                )
        except Exception as exc:
            evidence.unexpected_failures += 1
            evidence.errors.append(
                f"expected {expected_code.value}, got {type(exc).__name__}: {exc}"
            )
        else:
            evidence.unexpected_failures += 1
            evidence.accepted_ids.append(envelope.message_id)
            evidence.errors.append(
                f"publish {envelope.message_id} unexpectedly succeeded during {expected_code.value}"
            )
        finally:
            evidence.publish_latency.append(time.perf_counter() - started)

    async def _next_delivery(
        self,
        stream: Any,
        spec: TransportFaultBenchmarkSpec,
        evidence: _ScenarioEvidence,
    ) -> Any:
        started = time.perf_counter()
        delivery = await asyncio.wait_for(anext(stream), timeout=spec.timeout_seconds)
        evidence.delivery_latency.append(time.perf_counter() - started)
        evidence.delivery_ids.append(delivery.envelope.message_id)
        return delivery

    @staticmethod
    def _envelope(index: int) -> TransportEnvelope:
        return TransportEnvelope(
            message_id=f"benchmark-message-{index}",
            message_type="benchmark.signal",
            kind=MessageKind.SIGNAL,
            payload_schema_version="1.0",
            source_component="benchmarking.transport_faults",
            correlation_id="benchmark-transport-fault",
            idempotency_key=f"benchmark-message-{index}",
            payload={"index": index},
        )
