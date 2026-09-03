"""Observability adapter for the currently available authorization provider contract."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    AuthorizationRequest,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import JsonValue

from .exporters import Telemetry
from .hierarchy import TraceHierarchy
from .models import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)


class ObservedAuthorizationProvider(AuthorizationProvider):
    """Instrument allow/deny authorization decisions without owning policy state.

    The current canonical authorization contract exposes only allow/deny. Approval
    lifecycle telemetry is intentionally left to #15 once that richer contract exists.
    """

    def __init__(
        self,
        provider: AuthorizationProvider,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        context = _authorization_context(request, self.descriptor.provider_id)
        audit_attributes: dict[str, JsonValue] = {
            "action": request.action,
            "principal_ref": request.principal_ref,
            "resource_ref": request.resource_ref,
            "provider_id": self.descriptor.provider_id,
        }

        async def operation() -> AuthorizationDecision:
            return await self._provider.authorize(request)

        decision = await self.hierarchy.observe(
            span_name="authorization.authorize",
            metric_prefix="platform.authorization",
            event_prefix="authorization",
            component=FailureComponent.AUTHORIZATION_APPROVAL,
            context=context,
            operation=operation,
            attributes={
                "action": request.action,
                "provider_id": self.descriptor.provider_id,
            },
        )

        outcome = TelemetryOutcome.SUCCEEDED if decision.allowed else TelemetryOutcome.FAILED
        failure = None
        severity = TelemetrySeverity.INFO
        event_name = "authorization.allowed"
        if not decision.allowed:
            failure = FailureClassification(
                component=FailureComponent.AUTHORIZATION_APPROVAL,
                code="authorization_denied",
                retryable=False,
            )
            severity = TelemetrySeverity.WARNING
            event_name = "authorization.denied"
            self._telemetry.metric(
                "platform.authorization.denied",
                1.0,
                context=context,
                attributes={"action": request.action},
            )

        self._telemetry.metric(
            "platform.authorization.decisions",
            1.0,
            context=context,
            attributes={"action": request.action, "allowed": decision.allowed},
        )
        self._telemetry.log(
            severity=severity,
            component=FailureComponent.AUTHORIZATION_APPROVAL,
            event_name=event_name,
            context=context,
            outcome=outcome,
            failure=failure,
            attributes=audit_attributes,
        )
        self._telemetry.timeline(
            event_name=event_name,
            component=FailureComponent.AUTHORIZATION_APPROVAL,
            context=context,
            outcome=outcome,
            failure=failure,
            attributes=audit_attributes,
        )
        return decision


def _authorization_context(request: AuthorizationRequest, provider_id: str) -> TelemetryContext:
    task_id = None
    run_id = None
    if request.resource_ref.startswith("task_"):
        task_id = request.resource_ref
    elif request.resource_ref.startswith("run_"):
        run_id = request.resource_ref
    elif request.context.correlation_id.startswith("task_"):
        task_id = request.context.correlation_id

    return TelemetryContext(
        project_id=request.context.project_id,
        task_id=task_id,
        run_id=run_id,
        correlation_id=request.context.correlation_id,
        causation_id=request.context.causation_id,
        provider_id=provider_id,
    )
