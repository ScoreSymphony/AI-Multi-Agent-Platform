from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    AuthorizationRequest,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import (
    FailureComponent,
    InMemoryExporter,
    ObservedAuthorizationProvider,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TraceHierarchy,
)


class _AuthorizationProvider(AuthorizationProvider):
    def __init__(self, *, allowed: bool, fail: bool = False) -> None:
        self._allowed = allowed
        self._fail = fail

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="reference-authz",
            provider_type="authorization",
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        del request
        if self._fail:
            raise RuntimeError("private provider detail")
        return AuthorizationDecision(
            allowed=self._allowed,
            reason="internal policy detail that observability must not copy",
        )


def _request(task_id: str) -> AuthorizationRequest:
    return AuthorizationRequest(
        principal_ref="user:alice",
        action="task.execute",
        resource_ref=task_id,
        context=OperationContext(
            correlation_id=task_id,
            causation_id="authorization-check",
        ),
    )


def test_allow_and_deny_decisions_attach_to_task_trace_and_emit_safe_audit_events() -> None:
    task_id = new_id("task")
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    task_span = telemetry.start_span(
        "task.lifecycle",
        context=TelemetryContext(task_id=task_id, correlation_id=task_id),
    )
    telemetry.set_anchor("task", task_id, task_span)
    hierarchy = TraceHierarchy(telemetry)
    allowed = ObservedAuthorizationProvider(
        _AuthorizationProvider(allowed=True), telemetry, hierarchy=hierarchy
    )
    denied = ObservedAuthorizationProvider(
        _AuthorizationProvider(allowed=False), telemetry, hierarchy=hierarchy
    )

    async def scenario() -> None:
        assert (await allowed.authorize(_request(task_id))).allowed is True
        assert (await denied.authorize(_request(task_id))).allowed is False

    asyncio.run(scenario())

    spans = [span for span in exporter.spans if span.name == "authorization.authorize"]
    assert len(spans) == 2
    assert all(span.trace_id == task_span.trace_id for span in spans)
    assert all(span.parent_span_id == task_span.span_id for span in spans)
    assert all(span.context.task_id == task_id for span in spans)

    decisions = [
        metric for metric in exporter.metrics if metric.name == "platform.authorization.decisions"
    ]
    assert len(decisions) == 2
    assert {metric.attributes["allowed"] for metric in decisions} == {True, False}
    denied_metrics = [
        metric for metric in exporter.metrics if metric.name == "platform.authorization.denied"
    ]
    assert len(denied_metrics) == 1

    denied_event = next(entry for entry in exporter.timeline if entry.event_name == "authorization.denied")
    assert denied_event.outcome is TelemetryOutcome.FAILED
    assert denied_event.failure is not None
    assert denied_event.failure.component is FailureComponent.AUTHORIZATION_APPROVAL
    assert denied_event.failure.code == "authorization_denied"
    assert denied_event.attributes["principal_ref"] == "user:alice"
    assert denied_event.attributes["resource_ref"] == task_id

    serialized = repr(exporter.logs) + repr(exporter.timeline)
    assert "internal policy detail" not in serialized


def test_authorization_provider_failure_is_classified_at_authorization_layer() -> None:
    task_id = new_id("task")
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    observed = ObservedAuthorizationProvider(_AuthorizationProvider(allowed=False, fail=True), telemetry)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="private provider detail"):
            await observed.authorize(_request(task_id))

    asyncio.run(scenario())
    failed_span = next(span for span in exporter.spans if span.name == "authorization.authorize")
    assert failed_span.outcome is TelemetryOutcome.FAILED
    assert failed_span.failure is not None
    assert failed_span.failure.component is FailureComponent.AUTHORIZATION_APPROVAL
    assert failed_span.failure.code == "RuntimeError"
    failed_log = next(log for log in exporter.logs if log.event_name == "authorization.failed")
    assert failed_log.failure is not None
    assert failed_log.failure.component is FailureComponent.AUTHORIZATION_APPROVAL
    assert "private provider detail" not in repr(failed_log)
