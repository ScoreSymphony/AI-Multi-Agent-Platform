"""Single-node startup reconciliation for restart-sensitive transient authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol

from ai_multi_agent_platform.automation import AutomationStartupRecoveryReport
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.security.authentication_models import BrowserSession

from .startup_recovery import StartupRecoveryExtensionReport


class AutomationStartupRecovery(Protocol):
    """Narrow Automation owner seam used by the deployment startup gate."""

    async def reconcile_startup_deliveries(self) -> AutomationStartupRecoveryReport: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class SingleNodeTransientStateRecoveryExtension:
    """Reconcile restart-sensitive state without becoming a second lifecycle owner.

    Worker/Run and Plan/Step recovery remain owned by the canonical startup path.
    Workspace/filesystem cleanup remains with the persistence owner. This extension
    handles Automation delivery ownership and proves that durable authentication
    session revocation/expiry metadata survived the process restart.
    """

    automation: AutomationStartupRecovery
    authentication_sessions: Mapping[str, BrowserSession]
    clock: Callable[[], datetime] = _utc_now

    async def reconcile_startup(self) -> StartupRecoveryExtensionReport:
        started = monotonic()
        try:
            automation_report = await self.automation.reconcile_startup_deliveries()
        except ContractError as exc:
            return self._blocked_reconciliation_report(
                disposition="blocked_reconciliation_error",
                error_code=exc.code.value,
                started=started,
            )
        # error-boundary: allow-broad-catch=boundary startup reconciliation must fail closed
        except Exception:
            return self._blocked_reconciliation_report(
                disposition="blocked_unexpected_reconciliation_error",
                error_code="unexpected_reconciliation_error",
                started=started,
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
        evidence.append(
            {
                "state_class": "transient_state_reconciliation",
                "disposition": "blocked" if automation_report.blockers else "completed",
                "automation_candidates": len(automation_report.records),
                "authentication_sessions": len(self.authentication_sessions),
                "duration_ms": max(0.0, (monotonic() - started) * 1000),
            }
        )
        return StartupRecoveryExtensionReport(
            name="single-node-transient-state",
            items_checked=len(automation_report.records) + len(self.authentication_sessions),
            failures=failures,
            ready_for_service=automation_report.ready_for_service,
            evidence=tuple(evidence),
        )

    @staticmethod
    def _blocked_reconciliation_report(
        *,
        disposition: str,
        error_code: str,
        started: float,
    ) -> StartupRecoveryExtensionReport:
        return StartupRecoveryExtensionReport(
            name="single-node-transient-state",
            items_checked=0,
            failures=(
                {
                    "state_class": "automation_delivery",
                    "disposition": disposition,
                    "error_code": error_code,
                },
            ),
            ready_for_service=False,
            evidence=(
                {
                    "state_class": "transient_state_reconciliation",
                    "disposition": "blocked",
                    "automation_candidates": 0,
                    "authentication_sessions": 0,
                    "duration_ms": max(0.0, (monotonic() - started) * 1000),
                },
            ),
        )


__all__ = ["SingleNodeTransientStateRecoveryExtension"]
