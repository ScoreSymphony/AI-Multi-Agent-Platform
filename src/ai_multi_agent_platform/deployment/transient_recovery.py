"""Single-node startup reconciliation for restart-sensitive transient authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ai_multi_agent_platform.automation import (
    AutomationStartupRecoveryReport,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.security import BrowserSession

from .startup_recovery import StartupRecoveryExtensionReport


class AutomationStartupRecovery(Protocol):
    """Narrow Automation owner seam used by the deployment startup gate."""

    async def reconcile_startup_deliveries(self) -> AutomationStartupRecoveryReport: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class SingleNodeTransientStateRecoveryExtension:
    """Reconcile restart-sensitive state without becoming a second lifecycle owner.

    Worker/Run and Plan/Step recovery remain owned by the outer #707 startup path.
    Workspace/filesystem cleanup remains with the #1155 persistence owner. This
    extension handles Automation delivery ownership and proves that durable #36
    browser-session revocation/expiry metadata survived the process restart.
    """

    automation: AutomationStartupRecovery
    authentication_sessions: Mapping[str, BrowserSession]
    clock: Callable[[], datetime] = _utc_now

    async def reconcile_startup(self) -> StartupRecoveryExtensionReport:
        try:
            automation_report = await self.automation.reconcile_startup_deliveries()
        except ContractError as exc:
            return StartupRecoveryExtensionReport(
                name="single-node-transient-state",
                items_checked=0,
                failures=(
                    {
                        "state_class": "automation_delivery",
                        "disposition": "blocked_reconciliation_error",
                        "error_code": exc.code.value,
                    },
                ),
                ready_for_service=False,
            )

        failures = tuple(
            {
                "state_class": "automation_delivery",
                "delivery_id": record.delivery_id,
                "automation_id": record.automation_id,
                "disposition": record.disposition.value,
            }
            for record in automation_report.blockers
        )
        evidence: list[dict[str, object]] = [
            {
                "state_class": "automation_delivery",
                "delivery_id": record.delivery_id,
                "automation_id": record.automation_id,
                "before_status": record.before_status.value,
                "after_status": record.after_status.value,
                "disposition": record.disposition.value,
                "blocking": record.blocking,
                "generated_task_id": record.generated_task_id,
            }
            for record in automation_report.records
        ]

        now = self.clock().astimezone(UTC)
        active_sessions = 0
        expired_sessions = 0
        revoked_sessions = 0
        for session in self.authentication_sessions.values():
            if session.revoked_at is not None:
                revoked_sessions += 1
            elif now >= session.expires_at.astimezone(UTC):
                expired_sessions += 1
            else:
                active_sessions += 1

        evidence.append(
            {
                "state_class": "authentication_session",
                "disposition": "durable_authority_revalidated",
                "checked": len(self.authentication_sessions),
                "active": active_sessions,
                "expired": expired_sessions,
                "revoked": revoked_sessions,
            }
        )
        return StartupRecoveryExtensionReport(
            name="single-node-transient-state",
            items_checked=len(automation_report.records) + len(self.authentication_sessions),
            failures=failures,
            ready_for_service=automation_report.ready_for_service,
            evidence=tuple(evidence),
        )


__all__ = ["SingleNodeTransientStateRecoveryExtension"]
