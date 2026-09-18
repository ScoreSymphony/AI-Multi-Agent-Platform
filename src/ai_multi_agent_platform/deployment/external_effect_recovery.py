"""Startup reconciliation adapter for durable external capability effects."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.capabilities import (
    ExternalEffectRecoveryCoordinator,
    external_effect_recovery_resource,
)

from .startup_recovery import StartupRecoveryExtensionReport


class ExternalEffectStartupRecovery:
    """Reconcile provider-observable effects without blocking operator access to the Control Plane."""

    def __init__(self, recovery: ExternalEffectRecoveryCoordinator) -> None:
        self.recovery = recovery

    async def reconcile_startup(self) -> StartupRecoveryExtensionReport:
        before = tuple(record for record in self.recovery.list_records() if not record.terminal)
        reconciled = await self.recovery.reconcile_all()
        unresolved = tuple(record for record in reconciled if not record.terminal)
        failures: tuple[dict[str, Any], ...] = tuple(
            {
                "external_effect_id": record.effect_id,
                "task_id": record.task_id,
                "run_id": record.run_id,
                "capability_id": record.capability_id,
                "provider_id": record.provider_id,
                "disposition": record.disposition.value,
                "reason": record.reason,
                "permitted_actions": list(record.permitted_actions),
            }
            for record in unresolved
        )
        # Uncertain effects block replay of that exact action, not the whole Control Plane. Operators
        # need the northbound diagnostics to reconcile/confirm them after startup.
        return StartupRecoveryExtensionReport(
            name="external-effect-recovery",
            items_checked=len(before),
            failures=failures,
            ready_for_service=True,
        )


__all__ = ["ExternalEffectStartupRecovery"]
