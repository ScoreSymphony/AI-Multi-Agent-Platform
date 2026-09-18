"""Single-node persistence/filesystem readiness diagnostics and fail-closed health."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ai_multi_agent_platform.backup.inventory import SINGLE_NODE_DURABLE_STORES
from ai_multi_agent_platform.contracts import HealthStatus, ProviderContract, ProviderDescriptor
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureClassification,
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

from .config import SingleNodeConfig

_MIB = 1024 * 1024
_DEFAULT_WARNING_FREE_BYTES = 256 * _MIB
_DEFAULT_MINIMUM_FREE_BYTES = 64 * _MIB


@dataclass(frozen=True, slots=True)
class PersistenceDiagnostic:
    """Redacted operator-facing persistence diagnostic."""

    code: str
    component: str
    severity: str
    retryable: bool
    action: str

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "component": self.component,
            "severity": self.severity,
            "retryable": self.retryable,
            "action": self.action,
        }


class SingleNodePersistenceHealthProvider(ProviderContract):
    """Probe required single-node durable state without becoming its lifecycle owner."""

    def __init__(
        self,
        config: SingleNodeConfig,
        *,
        telemetry: Telemetry | None = None,
        warning_free_bytes: int = _DEFAULT_WARNING_FREE_BYTES,
        minimum_free_bytes: int = _DEFAULT_MINIMUM_FREE_BYTES,
        sqlite_timeout_seconds: float = 0.25,
        required_store_owners: frozenset[str] | None = None,
    ) -> None:
        if minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must be >= 0")
        if warning_free_bytes < minimum_free_bytes:
            raise ValueError("warning_free_bytes must be >= minimum_free_bytes")
        if sqlite_timeout_seconds <= 0:
            raise ValueError("sqlite_timeout_seconds must be > 0")
        self._config = config
        self._telemetry = telemetry
        self._warning_free_bytes = warning_free_bytes
        self._minimum_free_bytes = minimum_free_bytes
        self._sqlite_timeout_seconds = sqlite_timeout_seconds
        self._required_store_owners = required_store_owners
        self._status = HealthStatus.UNKNOWN
        self._diagnostics: tuple[PersistenceDiagnostic, ...] = ()
        self._checked_store_count = 0
        self._free_space_state = "unknown"

    @property
    def descriptor(self) -> ProviderDescriptor:
        resources: dict[str, JsonValue] = {
            "checked_store_count": self._checked_store_count,
            "free_space_state": self._free_space_state,
        }
        return ProviderDescriptor(
            provider_id="single-node-persistence",
            provider_type="persistence",
            supported_operations=("health",),
            health=self._status,
            resources=resources,
        )

    @property
    def health_diagnostics(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(item.as_json() for item in self._diagnostics)

    async def health(self) -> HealthStatus:
        started = time.monotonic()
        previous = self._status
        status, diagnostics, checked_store_count, free_space_state = await asyncio.to_thread(
            self._probe
        )
        self._status = status
        self._diagnostics = diagnostics
        self._checked_store_count = checked_store_count
        self._free_space_state = free_space_state
        self._observe_transition(previous, status, time.monotonic() - started)
        return status

    def _probe(
        self,
    ) -> tuple[HealthStatus, tuple[PersistenceDiagnostic, ...], int, str]:
        diagnostics: list[PersistenceDiagnostic] = []
        checked_store_count = 0
        data_root = self._config.data_dir

        required_directories = (
            ("data-root", data_root),
            ("database-root", self._config.database_dir),
            ("file-root", self._config.files_dir),
            ("workspace-root", self._config.workspaces_dir),
        )
        for component, path in required_directories:
            diagnostics.extend(self._probe_directory(path, component))

        free_space_state = self._probe_free_space(data_root, diagnostics)

        for spec in SINGLE_NODE_DURABLE_STORES:
            path = data_root / spec.path
            required_for_profile = spec.required and (
                self._required_store_owners is None
                or spec.owner in self._required_store_owners
            )
            if not path.exists():
                if required_for_profile:
                    diagnostics.append(
                        PersistenceDiagnostic(
                            code="required_store_missing",
                            component=spec.path,
                            severity="unavailable",
                            retryable=False,
                            action="restore or recreate the required store through its owning subsystem",
                        )
                    )
                continue

            checked_store_count += 1
            severity = "unavailable" if required_for_profile else "degraded"
            if spec.kind == "sqlite":
                diagnostic = self._probe_sqlite(path, spec.path, severity)
            else:
                diagnostic = self._probe_json(path, spec.path, severity)
            if diagnostic is not None:
                diagnostics.append(diagnostic)

        status = self._status_for(diagnostics)
        return status, tuple(diagnostics), checked_store_count, free_space_state

    def _probe_directory(
        self,
        path: Path,
        component: str,
    ) -> tuple[PersistenceDiagnostic, ...]:
        if not path.exists() or not path.is_dir():
            return (
                PersistenceDiagnostic(
                    code="persistence_path_unavailable",
                    component=component,
                    severity="unavailable",
                    retryable=True,
                    action="restore access to the required persistence directory and retry",
                ),
            )

        token = uuid4().hex
        pending = path / f".ai-map-health-{token}.pending"
        committed = path / f".ai-map-health-{token}.committed"
        try:
            with pending.open("xb") as handle:
                handle.write(b"persistence-health\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, committed)
            committed.unlink()
        except OSError:
            self._safe_unlink(pending)
            self._safe_unlink(committed)
            return (
                PersistenceDiagnostic(
                    code="persistence_path_unwritable",
                    component=component,
                    severity="unavailable",
                    retryable=True,
                    action="restore write/rename/delete permission and filesystem availability",
                ),
            )
        return ()

    def _probe_free_space(
        self,
        data_root: Path,
        diagnostics: list[PersistenceDiagnostic],
    ) -> str:
        try:
            free = shutil.disk_usage(data_root).free
        except OSError:
            diagnostics.append(
                PersistenceDiagnostic(
                    code="free_space_probe_failed",
                    component="data-root",
                    severity="unavailable",
                    retryable=True,
                    action="restore filesystem access before serving authoritative readiness",
                )
            )
            return "unknown"

        if free < self._minimum_free_bytes:
            diagnostics.append(
                PersistenceDiagnostic(
                    code="free_space_critical",
                    component="data-root",
                    severity="unavailable",
                    retryable=True,
                    action="free disk space before allowing new durable mutations",
                )
            )
            return "critical"
        if free < self._warning_free_bytes:
            diagnostics.append(
                PersistenceDiagnostic(
                    code="free_space_low",
                    component="data-root",
                    severity="degraded",
                    retryable=True,
                    action="free disk space before the minimum persistence reserve is reached",
                )
            )
            return "low"
        return "healthy"

    def _probe_sqlite(
        self,
        path: Path,
        component: str,
        severity: str,
    ) -> PersistenceDiagnostic | None:
        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(
                uri,
                uri=True,
                timeout=self._sqlite_timeout_seconds,
            ) as connection:
                row = connection.execute("PRAGMA quick_check(1)").fetchone()
            if row is None or str(row[0]).casefold() != "ok":
                return PersistenceDiagnostic(
                    code="sqlite_integrity_failed",
                    component=component,
                    severity=severity,
                    retryable=False,
                    action="stop writes and recover this store through its owning subsystem",
                )
        except sqlite3.OperationalError as exc:
            message = str(exc).casefold()
            retryable = "locked" in message or "busy" in message
            return PersistenceDiagnostic(
                code="sqlite_temporarily_unavailable" if retryable else "sqlite_open_failed",
                component=component,
                severity=severity,
                retryable=retryable,
                action=(
                    "clear the transient SQLite contention and retry"
                    if retryable
                    else "restore access to the SQLite store and retry"
                ),
            )
        except sqlite3.DatabaseError:
            return PersistenceDiagnostic(
                code="sqlite_integrity_failed",
                component=component,
                severity=severity,
                retryable=False,
                action="stop writes and recover this store through its owning subsystem",
            )
        return None

    @staticmethod
    def _probe_json(
        path: Path,
        component: str,
        severity: str,
    ) -> PersistenceDiagnostic | None:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return PersistenceDiagnostic(
                code="json_store_unavailable",
                component=component,
                severity=severity,
                retryable=True,
                action="restore filesystem access to the owning JSON store and retry",
            )
        except json.JSONDecodeError:
            return PersistenceDiagnostic(
                code="json_store_invalid",
                component=component,
                severity=severity,
                retryable=False,
                action="recover the invalid store through its owning subsystem",
            )
        return None

    @staticmethod
    def _status_for(diagnostics: list[PersistenceDiagnostic]) -> HealthStatus:
        if any(item.severity == "unavailable" for item in diagnostics):
            return HealthStatus.UNAVAILABLE
        if diagnostics:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    def _observe_transition(
        self,
        previous: HealthStatus,
        status: HealthStatus,
        duration_seconds: float,
    ) -> None:
        if self._telemetry is None or previous is status:
            return
        if status is HealthStatus.HEALTHY and previous in {
            HealthStatus.DEGRADED,
            HealthStatus.UNAVAILABLE,
        }:
            event_name = "persistence.recovered"
        elif status is HealthStatus.UNAVAILABLE:
            event_name = "persistence.unavailable"
        elif status is HealthStatus.DEGRADED:
            event_name = "persistence.degraded"
        else:
            event_name = "persistence.healthy"

        failure = None
        if status is not HealthStatus.HEALTHY and self._diagnostics:
            first = self._diagnostics[0]
            failure = FailureClassification(
                component=FailureComponent.PERSISTENCE_STORAGE,
                code=first.code,
                retryable=any(item.retryable for item in self._diagnostics),
            )
        self._telemetry.log(
            severity=(
                TelemetrySeverity.ERROR
                if status is HealthStatus.UNAVAILABLE
                else TelemetrySeverity.WARNING
                if status is HealthStatus.DEGRADED
                else TelemetrySeverity.INFO
            ),
            component=FailureComponent.PERSISTENCE_STORAGE,
            event_name=event_name,
            context=TelemetryContext(provider_id="single-node-persistence"),
            outcome=(
                TelemetryOutcome.SUCCEEDED
                if status is HealthStatus.HEALTHY
                else TelemetryOutcome.FAILED
            ),
            failure=failure,
            duration_seconds=duration_seconds,
            attributes={
                "status": status.value,
                "diagnostic_codes": [item.code for item in self._diagnostics],
                "checked_store_count": self._checked_store_count,
                "free_space_state": self._free_space_state,
            },
        )

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return


__all__ = ["PersistenceDiagnostic", "SingleNodePersistenceHealthProvider"]
