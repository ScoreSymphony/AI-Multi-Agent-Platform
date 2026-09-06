"""#89 Control Plane HA telemetry over the existing #16 observability facade."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureClassification,
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

from .contracts import AvailabilityMode, ControlPlaneRole, ReconciliationResult


class HighAvailabilityTelemetry:
    """Best-effort HA instrumentation without owning any exporter or backend.

    Telemetry must never participate in leadership correctness. Exporter failures are deliberately
    swallowed here so logging/metrics cannot change promotion, fencing, renewal or step-down
    semantics.
    """

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry

    def role_changed(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        previous_role: ControlPlaneRole,
        current_role: ControlPlaneRole,
        epoch: int,
        reason: str,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=current_role,
            epoch=epoch,
            extra={"previous_role": previous_role.value, "reason": reason},
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.role_transitions",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.timeline(
                event_name="control_plane.ha.role_changed",
                component=FailureComponent.CONTROL_PLANE_HA,
                context=TelemetryContext(),
                outcome=TelemetryOutcome.SUCCEEDED,
                attributes=attributes,
            )
        )

    def promotion_started(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        previous_epoch: int,
        reason: str,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=previous_epoch,
            extra={"reason": reason},
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.promotion_attempts",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.timeline(
                event_name="control_plane.ha.promotion_started",
                component=FailureComponent.CONTROL_PLANE_HA,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )

    def promotion_conflict(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        previous_epoch: int,
        reason: str,
        duration_seconds: float,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=previous_epoch,
            extra={"reason": reason},
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.promotion_conflicts",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.timeline(
                event_name="control_plane.ha.promotion_conflict",
                component=FailureComponent.CONTROL_PLANE_HA,
                context=TelemetryContext(),
                outcome=TelemetryOutcome.UNKNOWN,
                duration_seconds=duration_seconds,
                attributes=attributes,
            )
        )

    def promotion_completed(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        epoch: int,
        reason: str,
        duration_seconds: float,
        reconciliation: ReconciliationResult,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=epoch,
            extra={
                "reason": reason,
                "recovered_items": reconciliation.recovered_items,
                "rejected_stale_items": reconciliation.rejected_stale_items,
            },
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.promotions",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.promotion_duration_seconds",
                duration_seconds,
                context=TelemetryContext(),
                unit="seconds",
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.timeline(
                event_name="control_plane.ha.promotion_completed",
                component=FailureComponent.CONTROL_PLANE_HA,
                context=TelemetryContext(),
                outcome=TelemetryOutcome.SUCCEEDED,
                duration_seconds=duration_seconds,
                attributes=attributes,
            )
        )

    def promotion_failed(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        epoch: int,
        reason: str,
        failure_code: str,
        retryable: bool,
        duration_seconds: float,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=epoch,
            extra={"reason": reason, "failure_code": failure_code},
        )
        failure = FailureClassification(
            component=FailureComponent.CONTROL_PLANE_HA,
            code=failure_code,
            retryable=retryable,
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.promotion_failures",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.log(
                severity=TelemetrySeverity.ERROR,
                component=FailureComponent.CONTROL_PLANE_HA,
                event_name="control_plane.ha.promotion_failed",
                context=TelemetryContext(),
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                duration_seconds=duration_seconds,
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.timeline(
                event_name="control_plane.ha.promotion_failed",
                component=FailureComponent.CONTROL_PLANE_HA,
                context=TelemetryContext(),
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                duration_seconds=duration_seconds,
                attributes=attributes,
            )
        )

    def lease_renewed(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        epoch: int,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=epoch,
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.lease_renewals",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.timeline(
                event_name="control_plane.ha.lease_renewed",
                component=FailureComponent.CONTROL_PLANE_HA,
                context=TelemetryContext(),
                outcome=TelemetryOutcome.SUCCEEDED,
                attributes=attributes,
            )
        )

    def authority_rejected(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        epoch: int,
        operation: str,
        failure_code: str,
        retryable: bool,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=epoch,
            extra={"operation": operation, "failure_code": failure_code},
        )
        failure = FailureClassification(
            component=FailureComponent.CONTROL_PLANE_HA,
            code=failure_code,
            retryable=retryable,
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.authority_rejections",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.log(
                severity=TelemetrySeverity.WARNING,
                component=FailureComponent.CONTROL_PLANE_HA,
                event_name="control_plane.ha.authority_rejected",
                context=TelemetryContext(),
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                attributes=attributes,
            )
        )

    def coordination_failure(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        role: ControlPlaneRole,
        epoch: int,
        operation: str,
        failure_code: str,
        retryable: bool = True,
    ) -> None:
        attributes = _attributes(
            instance_id=instance_id,
            mode=mode,
            role=role,
            epoch=epoch,
            extra={"operation": operation, "failure_code": failure_code},
        )
        failure = FailureClassification(
            component=FailureComponent.CONTROL_PLANE_HA,
            code=failure_code,
            retryable=retryable,
        )
        self._best_effort(
            lambda: self.telemetry.metric(
                "platform.control_plane.ha.coordination_failures",
                1.0,
                context=TelemetryContext(),
                attributes=attributes,
            )
        )
        self._best_effort(
            lambda: self.telemetry.log(
                severity=TelemetrySeverity.ERROR,
                component=FailureComponent.CONTROL_PLANE_HA,
                event_name="control_plane.ha.coordination_failed",
                context=TelemetryContext(),
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                attributes=attributes,
            )
        )

    @staticmethod
    def _best_effort(action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:
            return


def _attributes(
    *,
    instance_id: str,
    mode: AvailabilityMode,
    role: ControlPlaneRole,
    epoch: int,
    extra: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    attributes: dict[str, JsonValue] = {
        "instance_id": instance_id,
        "mode": mode.value,
        "role": role.value,
        "epoch": epoch,
    }
    if extra:
        attributes.update(extra)
    return attributes
