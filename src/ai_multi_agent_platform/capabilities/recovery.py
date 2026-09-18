"""Durable recovery semantics for uncertain external capability side effects."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue

from .invocation import InvocationObserver, NullInvocationObserver
from .types import (
    CapabilityInvocation,
    CapabilityRegistration,
    CapabilitySpec,
    ExternalEffectIdempotency,
    ExternalEffectReconciliationSupport,
    InvocationRecord,
    InvocationStatus,
    SideEffectClassification,
)


class ExternalEffectRecoveryDisposition(StrEnum):
    SAFE_TO_RESUME = "safe_to_resume"
    SAFE_TO_RETRY = "safe_to_retry"
    RECONCILE_WITH_PROVIDER = "reconcile_with_provider"
    TERMINAL_SUCCESS = "terminal_success"
    TERMINAL_FAILURE = "terminal_failure"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    UNCERTAIN_MANUAL_REVIEW = "uncertain_manual_review"
    BLOCKED_OPERATOR_ACTION = "blocked_operator_action"


class ExternalEffectRecoveryStatus(StrEnum):
    DISPATCHING = "dispatching"
    RECONCILING = "reconciling"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExternalEffectObservationStatus(StrEnum):
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExternalEffectReconciliationRequest:
    """Private read-only reconciliation request.

    The idempotency key and provider metadata are recovery inputs, not canonical public identity.
    """

    effect_id: str
    invocation_id: str
    capability_id: str
    capability_version: str
    provider_id: str
    provider_tool_ref: str
    idempotency_key: str | None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalEffectObservation:
    status: ExternalEffectObservationStatus
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()
    detail: str | None = None


@runtime_checkable
class ExternalEffectReconciler(Protocol):
    """Optional provider seam for repeat-safe, observation-only reconciliation."""

    async def reconcile_external_effect(
        self,
        request: ExternalEffectReconciliationRequest,
    ) -> ExternalEffectObservation: ...


@dataclass(frozen=True, slots=True)
class ExternalEffectRecoveryEvent:
    """Content-free recovery transition suitable for #16 telemetry/timeline export."""

    event_name: str
    effect_id: str
    invocation_id: str
    task_id: str
    run_id: str
    capability_id: str
    provider_id: str
    status: ExternalEffectRecoveryStatus
    disposition: ExternalEffectRecoveryDisposition
    reason: str
    occurred_at: datetime


class ExternalEffectRecoveryEventObserver(Protocol):
    async def record_external_effect_recovery(
        self,
        event: ExternalEffectRecoveryEvent,
    ) -> None: ...


class NullExternalEffectRecoveryEventObserver:
    async def record_external_effect_recovery(
        self,
        event: ExternalEffectRecoveryEvent,
    ) -> None:
        del event


@dataclass(frozen=True, slots=True)
class ExternalEffectRecoveryRecord:
    effect_id: str
    invocation_id: str
    canonical_tool_invocation_id: str | None
    task_id: str
    run_id: str
    capability_id: str
    capability_version: str
    provider_id: str
    provider_tool_ref: str
    side_effects: SideEffectClassification
    idempotency: ExternalEffectIdempotency
    reconciliation_support: ExternalEffectReconciliationSupport
    idempotency_key: str | None
    status: ExternalEffectRecoveryStatus
    disposition: ExternalEffectRecoveryDisposition
    reason: str
    dispatch_attempts: int = 1
    reconciliation_attempts: int = 0
    duplicate_callbacks_ignored: int = 0
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()
    last_operator_actor: str | None = None
    last_operator_reason: str | None = None
    created_at: datetime = datetime.min.replace(tzinfo=UTC)
    updated_at: datetime = datetime.min.replace(tzinfo=UTC)

    @property
    def terminal(self) -> bool:
        return self.status in {
            ExternalEffectRecoveryStatus.SUCCEEDED,
            ExternalEffectRecoveryStatus.FAILED,
        }

    @property
    def permitted_actions(self) -> tuple[str, ...]:
        if self.terminal:
            return ()
        actions = ["mark_failed", "confirm_succeeded"]
        if self.disposition in {
            ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER,
            ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY,
            ExternalEffectRecoveryDisposition.SAFE_TO_RESUME,
        }:
            actions.insert(0, "reconcile")
        if self.disposition in {
            ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW,
            ExternalEffectRecoveryDisposition.BLOCKED_OPERATOR_ACTION,
        }:
            actions.insert(0, "authorize_retry")
        if self.disposition is ExternalEffectRecoveryDisposition.SAFE_TO_RETRY:
            actions.insert(0, "retry")
        return tuple(actions)


@dataclass(frozen=True, slots=True)
class _PendingExternalAttempt:
    invocation_id: str
    task_id: str
    run_id: str
    capability_id: str
    capability_version: str
    provider_id: str
    provider_tool_ref: str
    side_effects: SideEffectClassification
    idempotency: ExternalEffectIdempotency
    reconciliation_support: ExternalEffectReconciliationSupport
    idempotency_key: str | None


class ExternalEffectRecoveryRepository(Protocol):
    def get(self, effect_id: str) -> ExternalEffectRecoveryRecord: ...
    def find_by_invocation(self, invocation_id: str) -> ExternalEffectRecoveryRecord | None: ...
    def find_by_idempotency(
        self,
        provider_id: str,
        capability_id: str,
        idempotency_key: str,
    ) -> ExternalEffectRecoveryRecord | None: ...
    def list(self) -> tuple[ExternalEffectRecoveryRecord, ...]: ...
    def save(self, record: ExternalEffectRecoveryRecord) -> ExternalEffectRecoveryRecord: ...


class InMemoryExternalEffectRecoveryRepository:
    def __init__(self) -> None:
        self._records: dict[str, ExternalEffectRecoveryRecord] = {}

    def get(self, effect_id: str) -> ExternalEffectRecoveryRecord:
        try:
            return self._records[effect_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "external effect recovery record not found",
            ) from exc

    def find_by_invocation(self, invocation_id: str) -> ExternalEffectRecoveryRecord | None:
        return next(
            (record for record in self._records.values() if record.invocation_id == invocation_id),
            None,
        )

    def find_by_idempotency(
        self,
        provider_id: str,
        capability_id: str,
        idempotency_key: str,
    ) -> ExternalEffectRecoveryRecord | None:
        return next(
            (
                record
                for record in self._records.values()
                if record.provider_id == provider_id
                and record.capability_id == capability_id
                and record.idempotency_key == idempotency_key
            ),
            None,
        )

    def list(self) -> tuple[ExternalEffectRecoveryRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda record: record.effect_id))

    def save(self, record: ExternalEffectRecoveryRecord) -> ExternalEffectRecoveryRecord:
        existing = self._records.get(record.effect_id)
        if existing is not None and existing.invocation_id != record.invocation_id:
            raise ContractError(ErrorCode.CONFLICT, "external effect identity drift")
        duplicate = self.find_by_invocation(record.invocation_id)
        if duplicate is not None and duplicate.effect_id != record.effect_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external invocation already has a recovery record",
            )
        self._records[record.effect_id] = record
        return record


class SQLiteExternalEffectRecoveryRepository:
    """Restart-safe journal that deliberately does not persist invocation arguments."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS external_effect_recovery (
                    effect_id TEXT PRIMARY KEY,
                    invocation_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS external_effect_recovery_idempotency
                ON external_effect_recovery(provider_id, capability_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, effect_id: str) -> ExternalEffectRecoveryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM external_effect_recovery WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "external effect recovery record not found",
            )
        return _record_from_json(_load(row[0]))

    def find_by_invocation(self, invocation_id: str) -> ExternalEffectRecoveryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM external_effect_recovery WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        return None if row is None else _record_from_json(_load(row[0]))

    def find_by_idempotency(
        self,
        provider_id: str,
        capability_id: str,
        idempotency_key: str,
    ) -> ExternalEffectRecoveryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM external_effect_recovery
                WHERE provider_id = ? AND capability_id = ? AND idempotency_key = ?
                """,
                (provider_id, capability_id, idempotency_key),
            ).fetchone()
        return None if row is None else _record_from_json(_load(row[0]))

    def list(self) -> tuple[ExternalEffectRecoveryRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM external_effect_recovery ORDER BY effect_id"
            ).fetchall()
        return tuple(_record_from_json(_load(row[0])) for row in rows)

    def save(self, record: ExternalEffectRecoveryRecord) -> ExternalEffectRecoveryRecord:
        payload = json.dumps(_record_to_json(record), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO external_effect_recovery(
                        effect_id, invocation_id, provider_id, capability_id, idempotency_key, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(effect_id) DO UPDATE SET
                        invocation_id = excluded.invocation_id,
                        provider_id = excluded.provider_id,
                        capability_id = excluded.capability_id,
                        idempotency_key = excluded.idempotency_key,
                        payload = excluded.payload
                    """,
                    (
                        record.effect_id,
                        record.invocation_id,
                        record.provider_id,
                        record.capability_id,
                        record.idempotency_key,
                        payload,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external effect recovery identity/idempotency conflict",
            ) from exc
        return record


class ExternalEffectRecoveryCoordinator:
    """Classify ambiguous outcomes without becoming a second execution/retry engine."""

    def __init__(
        self,
        repository: ExternalEffectRecoveryRepository,
        *,
        event_observer: ExternalEffectRecoveryEventObserver | None = None,
    ) -> None:
        self.repository = repository
        self._event_observer = event_observer or NullExternalEffectRecoveryEventObserver()
        self._pending: dict[str, _PendingExternalAttempt] = {}
        self._reconcilers: dict[str, ExternalEffectReconciler] = {}
        self._lock = asyncio.Lock()

    async def _emit(
        self,
        event_name: str,
        record: ExternalEffectRecoveryRecord,
    ) -> None:
        await self._event_observer.record_external_effect_recovery(
            ExternalEffectRecoveryEvent(
                event_name=event_name,
                effect_id=record.effect_id,
                invocation_id=record.invocation_id,
                task_id=record.task_id,
                run_id=record.run_id,
                capability_id=record.capability_id,
                provider_id=record.provider_id,
                status=record.status,
                disposition=record.disposition,
                reason=record.reason,
                occurred_at=record.updated_at,
            )
        )

    def register_reconciler(
        self,
        provider_id: str,
        reconciler: ExternalEffectReconciler,
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id must not be blank")
        existing = self._reconcilers.get(provider_id)
        if existing is reconciler:
            return
        if existing is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external effect reconciler already registered",
            )
        self._reconcilers[provider_id] = reconciler

    async def prepare_attempt(
        self,
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        registration: CapabilityRegistration,
    ) -> None:
        if capability.side_effects not in {
            SideEffectClassification.EXTERNAL,
            SideEffectClassification.DESTRUCTIVE,
        }:
            return
        pending = _PendingExternalAttempt(
            invocation_id=request.invocation_id,
            task_id=request.trace.task_id,
            run_id=request.trace.run_id,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            provider_id=registration.provider_id,
            provider_tool_ref=registration.provider_tool_ref,
            side_effects=capability.side_effects,
            idempotency=capability.external_effect_recovery.idempotency,
            reconciliation_support=capability.external_effect_recovery.reconciliation,
            idempotency_key=request.context.control.idempotency_key,
        )
        async with self._lock:
            existing = self.repository.find_by_invocation(request.invocation_id)
            if pending.idempotency_key is not None:
                keyed = self.repository.find_by_idempotency(
                    pending.provider_id,
                    pending.capability_id,
                    pending.idempotency_key,
                )
                if keyed is not None and existing is None:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "idempotency key cannot migrate to a new canonical invocation",
                        details={"external_effect_id": keyed.effect_id},
                    )
                if (
                    keyed is not None
                    and existing is not None
                    and keyed.effect_id != existing.effect_id
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "idempotency key is already bound to another external effect",
                    )
            if existing is not None:
                self._require_retry_allowed(existing, pending)
            self._pending[request.invocation_id] = pending

    async def discard_unstarted_attempt(self, invocation_id: str) -> None:
        async with self._lock:
            self._pending.pop(invocation_id, None)

    async def observe(self, record: InvocationRecord) -> None:
        async with self._lock:
            if record.status is InvocationStatus.RUNNING:
                self._begin_dispatch(record)
                return
            existing = self.repository.find_by_invocation(record.invocation_id)
            if existing is None:
                self._pending.pop(record.invocation_id, None)
                return
            self._pending.pop(record.invocation_id, None)
            if existing.status is not ExternalEffectRecoveryStatus.DISPATCHING:
                if record.status in {
                    InvocationStatus.SUCCEEDED,
                    InvocationStatus.FAILED,
                    InvocationStatus.CANCELLED,
                    InvocationStatus.TIMED_OUT,
                }:
                    self.repository.save(
                        replace(
                            existing,
                            duplicate_callbacks_ignored=existing.duplicate_callbacks_ignored + 1,
                            updated_at=_utc_now(),
                        )
                    )
                return
            if record.status is InvocationStatus.SUCCEEDED:
                self.repository.save(
                    replace(
                        existing,
                        status=ExternalEffectRecoveryStatus.SUCCEEDED,
                        disposition=ExternalEffectRecoveryDisposition.TERMINAL_SUCCESS,
                        reason="provider_acknowledged_success",
                        adapter_metadata=record.adapter_metadata,
                        updated_at=_utc_now(),
                    )
                )
                return
            if record.status in {
                InvocationStatus.FAILED,
                InvocationStatus.CANCELLED,
                InvocationStatus.TIMED_OUT,
            }:
                disposition = self._uncertain_disposition(existing)
                status = (
                    ExternalEffectRecoveryStatus.UNCERTAIN
                    if disposition
                    in {
                        ExternalEffectRecoveryDisposition.SAFE_TO_RETRY,
                        ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER,
                        ExternalEffectRecoveryDisposition.SAFE_TO_RESUME,
                    }
                    else ExternalEffectRecoveryStatus.BLOCKED
                )
                self.repository.save(
                    replace(
                        existing,
                        status=status,
                        disposition=disposition,
                        reason=f"provider_outcome_unacknowledged:{record.error_code or record.status.value}",
                        adapter_metadata=record.adapter_metadata,
                        updated_at=_utc_now(),
                    )
                )

    async def reconcile_effect(self, effect_id: str) -> ExternalEffectRecoveryRecord:
        async with self._lock:
            record = self.repository.get(effect_id)
            if record.terminal:
                return record
            if record.disposition not in {
                ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER,
                ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY,
                ExternalEffectRecoveryDisposition.SAFE_TO_RESUME,
            }:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "external effect is not eligible for provider reconciliation",
                    details={"disposition": record.disposition.value},
                )
            reconciler = self._reconcilers.get(record.provider_id)
            if reconciler is None:
                return self.repository.save(
                    replace(
                        record,
                        status=ExternalEffectRecoveryStatus.BLOCKED,
                        disposition=ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY,
                        reason="declared_reconciler_not_available",
                        updated_at=_utc_now(),
                    )
                )
            record = self.repository.save(
                replace(
                    record,
                    status=ExternalEffectRecoveryStatus.RECONCILING,
                    reconciliation_attempts=record.reconciliation_attempts + 1,
                    reason="provider_reconciliation_started",
                    updated_at=_utc_now(),
                )
            )

        try:
            observation = await reconciler.reconcile_external_effect(
                ExternalEffectReconciliationRequest(
                    effect_id=record.effect_id,
                    invocation_id=record.invocation_id,
                    capability_id=record.capability_id,
                    capability_version=record.capability_version,
                    provider_id=record.provider_id,
                    provider_tool_ref=record.provider_tool_ref,
                    idempotency_key=record.idempotency_key,
                    adapter_metadata=record.adapter_metadata,
                )
            )
        except ContractError as exc:
            async with self._lock:
                current = self.repository.get(effect_id)
                disposition = (
                    ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY
                    if exc.retryable
                    or exc.code
                    in {ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT, ErrorCode.TRANSIENT_FAILURE}
                    else ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW
                )
                return self.repository.save(
                    replace(
                        current,
                        status=ExternalEffectRecoveryStatus.BLOCKED,
                        disposition=disposition,
                        reason=f"reconciliation_failed:{exc.code.value}",
                        updated_at=_utc_now(),
                    )
                )
        except Exception as exc:  # error-boundary: provider reconciliation outer boundary
            async with self._lock:
                current = self.repository.get(effect_id)
                self.repository.save(
                    replace(
                        current,
                        status=ExternalEffectRecoveryStatus.BLOCKED,
                        disposition=ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW,
                        reason="reconciliation_failed:backend_error",
                        updated_at=_utc_now(),
                    )
                )
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "external effect reconciler failed",
                provider_id=record.provider_id,
            ) from exc

        async with self._lock:
            current = self.repository.get(effect_id)
            return self.repository.save(_apply_observation(current, observation))

    async def reconcile_all(self) -> tuple[ExternalEffectRecoveryRecord, ...]:
        results: list[ExternalEffectRecoveryRecord] = []
        for record in self.repository.list():
            if record.terminal:
                continue
            if record.status is ExternalEffectRecoveryStatus.DISPATCHING:
                disposition = self._uncertain_disposition(record)
                record = self.repository.save(
                    replace(
                        record,
                        status=(
                            ExternalEffectRecoveryStatus.UNCERTAIN
                            if disposition
                            in {
                                ExternalEffectRecoveryDisposition.SAFE_TO_RETRY,
                                ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER,
                            }
                            else ExternalEffectRecoveryStatus.BLOCKED
                        ),
                        disposition=disposition,
                        reason="startup_detected_unacknowledged_dispatch",
                        updated_at=_utc_now(),
                    )
                )
            elif record.status is ExternalEffectRecoveryStatus.RECONCILING:
                record = self.repository.save(
                    replace(
                        record,
                        status=ExternalEffectRecoveryStatus.UNCERTAIN,
                        disposition=ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER,
                        reason="startup_resuming_interrupted_read_only_reconciliation",
                        updated_at=_utc_now(),
                    )
                )
            if record.disposition in {
                ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER,
                ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY,
                ExternalEffectRecoveryDisposition.SAFE_TO_RESUME,
            }:
                results.append(await self.reconcile_effect(record.effect_id))
            else:
                results.append(record)
        return tuple(results)

    async def authorize_retry(
        self,
        effect_id: str,
        *,
        actor: str,
        reason: str,
    ) -> ExternalEffectRecoveryRecord:
        return await self._operator_transition(
            effect_id,
            actor=actor,
            reason=reason,
            status=ExternalEffectRecoveryStatus.UNCERTAIN,
            disposition=ExternalEffectRecoveryDisposition.SAFE_TO_RETRY,
            transition_reason="operator_authorized_retry_after_review",
        )

    async def mark_failed(
        self,
        effect_id: str,
        *,
        actor: str,
        reason: str,
    ) -> ExternalEffectRecoveryRecord:
        return await self._operator_transition(
            effect_id,
            actor=actor,
            reason=reason,
            status=ExternalEffectRecoveryStatus.FAILED,
            disposition=ExternalEffectRecoveryDisposition.TERMINAL_FAILURE,
            transition_reason="operator_marked_failed",
        )

    async def confirm_succeeded(
        self,
        effect_id: str,
        *,
        actor: str,
        reason: str,
        result_ref: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> ExternalEffectRecoveryRecord:
        record = await self._operator_transition(
            effect_id,
            actor=actor,
            reason=reason,
            status=ExternalEffectRecoveryStatus.SUCCEEDED,
            disposition=ExternalEffectRecoveryDisposition.TERMINAL_SUCCESS,
            transition_reason="operator_confirmed_success",
        )
        async with self._lock:
            current = self.repository.get(record.effect_id)
            return self.repository.save(
                replace(
                    current,
                    result_ref=result_ref or current.result_ref,
                    evidence_refs=tuple(dict.fromkeys((*current.evidence_refs, *evidence_refs))),
                    updated_at=_utc_now(),
                )
            )

    def list_records(self) -> tuple[ExternalEffectRecoveryRecord, ...]:
        return self.repository.list()

    def get_record(self, effect_id: str) -> ExternalEffectRecoveryRecord:
        return self.repository.get(effect_id)

    def find_record_by_invocation(
        self,
        invocation_id: str,
    ) -> ExternalEffectRecoveryRecord | None:
        return self.repository.find_by_invocation(invocation_id)

    async def _operator_transition(
        self,
        effect_id: str,
        *,
        actor: str,
        reason: str,
        status: ExternalEffectRecoveryStatus,
        disposition: ExternalEffectRecoveryDisposition,
        transition_reason: str,
    ) -> ExternalEffectRecoveryRecord:
        if not actor.strip() or not reason.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "operator actor and reason are required",
            )
        async with self._lock:
            current = self.repository.get(effect_id)
            if current.terminal:
                if current.status is status:
                    return current
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "external effect already has a terminal outcome",
                )
            return self.repository.save(
                replace(
                    current,
                    status=status,
                    disposition=disposition,
                    reason=transition_reason,
                    last_operator_actor=actor,
                    last_operator_reason=reason,
                    updated_at=_utc_now(),
                )
            )

    def _begin_dispatch(self, record: InvocationRecord) -> None:
        pending = self._pending.get(record.invocation_id)
        if pending is None:
            return
        existing = self.repository.find_by_invocation(record.invocation_id)
        now = _utc_now()
        if existing is not None:
            self.repository.save(
                replace(
                    existing,
                    canonical_tool_invocation_id=(
                        record.canonical_tool_invocation_id
                        or existing.canonical_tool_invocation_id
                    ),
                    status=ExternalEffectRecoveryStatus.DISPATCHING,
                    reason="retry_dispatch_started",
                    dispatch_attempts=existing.dispatch_attempts + 1,
                    updated_at=now,
                )
            )
            return
        self.repository.save(
            ExternalEffectRecoveryRecord(
                effect_id=_effect_id(pending.provider_id, pending.invocation_id),
                invocation_id=pending.invocation_id,
                canonical_tool_invocation_id=record.canonical_tool_invocation_id,
                task_id=pending.task_id,
                run_id=pending.run_id,
                capability_id=pending.capability_id,
                capability_version=pending.capability_version,
                provider_id=pending.provider_id,
                provider_tool_ref=pending.provider_tool_ref,
                side_effects=pending.side_effects,
                idempotency=pending.idempotency,
                reconciliation_support=pending.reconciliation_support,
                idempotency_key=pending.idempotency_key,
                status=ExternalEffectRecoveryStatus.DISPATCHING,
                disposition=ExternalEffectRecoveryDisposition.BLOCKED_OPERATOR_ACTION,
                reason="provider_dispatch_started",
                adapter_metadata=record.adapter_metadata,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _uncertain_disposition(
        record: ExternalEffectRecoveryRecord,
    ) -> ExternalEffectRecoveryDisposition:
        if (
            record.idempotency is ExternalEffectIdempotency.GUARANTEED
            and record.idempotency_key is not None
        ):
            return ExternalEffectRecoveryDisposition.SAFE_TO_RETRY
        if (
            record.reconciliation_support
            is ExternalEffectReconciliationSupport.SUPPORTED
        ):
            return ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER
        return ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW

    @staticmethod
    def _require_retry_allowed(
        existing: ExternalEffectRecoveryRecord,
        pending: _PendingExternalAttempt,
    ) -> None:
        if (
            existing.provider_id != pending.provider_id
            or existing.capability_id != pending.capability_id
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "external effect retry changed provider/capability identity",
            )
        if existing.idempotency_key != pending.idempotency_key:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external effect retry changed its idempotency key",
            )
        if existing.disposition is not ExternalEffectRecoveryDisposition.SAFE_TO_RETRY:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external effect outcome is unresolved; blind replay is blocked",
                details={
                    "external_effect_id": existing.effect_id,
                    "recovery_disposition": existing.disposition.value,
                    "permitted_actions": list(existing.permitted_actions),
                },
            )


class ExternalEffectInvocationObserver:
    def __init__(
        self,
        recovery: ExternalEffectRecoveryCoordinator,
        delegate: InvocationObserver | None = None,
    ) -> None:
        self.recovery = recovery
        self.delegate = delegate or NullInvocationObserver()

    async def record(self, record: InvocationRecord) -> None:
        await self.recovery.observe(record)
        await self.delegate.record(record)


def external_effect_recovery_resource(
    record: ExternalEffectRecoveryRecord,
) -> dict[str, JsonValue]:
    """Redacted operator projection. Private idempotency/native metadata never cross northbound."""

    return {
        "id": record.effect_id,
        "type": "external_effect_recovery",
        "invocation_id": record.invocation_id,
        "canonical_tool_invocation_id": record.canonical_tool_invocation_id,
        "task_id": record.task_id,
        "run_id": record.run_id,
        "capability_id": record.capability_id,
        "capability_version": record.capability_version,
        "provider_id": record.provider_id,
        "side_effects": record.side_effects.value,
        "idempotency": record.idempotency.value,
        "reconciliation_support": record.reconciliation_support.value,
        "idempotency_key_present": record.idempotency_key is not None,
        "status": record.status.value,
        "disposition": record.disposition.value,
        "reason": record.reason,
        "dispatch_attempts": record.dispatch_attempts,
        "reconciliation_attempts": record.reconciliation_attempts,
        "duplicate_callbacks_ignored": record.duplicate_callbacks_ignored,
        "result_ref": record.result_ref,
        "artifact_refs": list(record.artifact_refs),
        "evidence_refs": list(record.evidence_refs),
        "adapter_metadata_namespaces": sorted(
            {metadata.namespace for metadata in record.adapter_metadata}
        ),
        "last_operator_actor": record.last_operator_actor,
        "last_operator_reason": record.last_operator_reason,
        "permitted_actions": list(record.permitted_actions),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _apply_observation(
    record: ExternalEffectRecoveryRecord,
    observation: ExternalEffectObservation,
) -> ExternalEffectRecoveryRecord:
    common = {
        "result_ref": observation.result_ref or record.result_ref,
        "artifact_refs": tuple(
            dict.fromkeys((*record.artifact_refs, *observation.artifact_refs))
        ),
        "evidence_refs": tuple(
            dict.fromkeys((*record.evidence_refs, *observation.evidence_refs))
        ),
        "adapter_metadata": observation.adapter_metadata or record.adapter_metadata,
        "updated_at": _utc_now(),
    }
    if observation.status is ExternalEffectObservationStatus.APPLIED:
        return replace(
            record,
            status=ExternalEffectRecoveryStatus.SUCCEEDED,
            disposition=ExternalEffectRecoveryDisposition.TERMINAL_SUCCESS,
            reason=observation.detail or "provider_reconciliation_confirmed_applied",
            **common,
        )
    if observation.status is ExternalEffectObservationStatus.NOT_APPLIED:
        return replace(
            record,
            status=ExternalEffectRecoveryStatus.UNCERTAIN,
            disposition=ExternalEffectRecoveryDisposition.SAFE_TO_RETRY,
            reason=observation.detail or "provider_reconciliation_confirmed_not_applied",
            **common,
        )
    if observation.status is ExternalEffectObservationStatus.IN_PROGRESS:
        return replace(
            record,
            status=ExternalEffectRecoveryStatus.UNCERTAIN,
            disposition=ExternalEffectRecoveryDisposition.SAFE_TO_RESUME,
            reason=observation.detail or "provider_reconciliation_confirmed_in_progress",
            **common,
        )
    if observation.status is ExternalEffectObservationStatus.FAILED:
        return replace(
            record,
            status=ExternalEffectRecoveryStatus.FAILED,
            disposition=ExternalEffectRecoveryDisposition.TERMINAL_FAILURE,
            reason=observation.detail or "provider_reconciliation_confirmed_failure",
            **common,
        )
    if observation.status is ExternalEffectObservationStatus.UNAVAILABLE:
        return replace(
            record,
            status=ExternalEffectRecoveryStatus.BLOCKED,
            disposition=ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY,
            reason=observation.detail or "provider_reconciliation_unavailable",
            **common,
        )
    return replace(
        record,
        status=ExternalEffectRecoveryStatus.BLOCKED,
        disposition=ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW,
        reason=observation.detail or "provider_reconciliation_outcome_unknown",
        **common,
    )


def _effect_id(provider_id: str, invocation_id: str) -> str:
    return f"external_effect_{uuid5(NAMESPACE_URL, f'{provider_id}:{invocation_id}')}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _metadata_to_json(metadata: tuple[AdapterMetadata, ...]) -> JsonValue:
    return [
        {"namespace": item.namespace, "values": dict(item.values)}
        for item in metadata
    ]


def _metadata_from_json(value: JsonValue) -> tuple[AdapterMetadata, ...]:
    if not isinstance(value, list):
        return ()
    items: list[AdapterMetadata] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        namespace = item.get("namespace")
        values = item.get("values")
        if isinstance(namespace, str) and isinstance(values, dict):
            items.append(
                AdapterMetadata(
                    namespace=namespace,
                    values=cast(dict[str, JsonValue], values),
                )
            )
    return tuple(items)


def _record_to_json(record: ExternalEffectRecoveryRecord) -> dict[str, JsonValue]:
    return {
        "effect_id": record.effect_id,
        "invocation_id": record.invocation_id,
        "canonical_tool_invocation_id": record.canonical_tool_invocation_id,
        "task_id": record.task_id,
        "run_id": record.run_id,
        "capability_id": record.capability_id,
        "capability_version": record.capability_version,
        "provider_id": record.provider_id,
        "provider_tool_ref": record.provider_tool_ref,
        "side_effects": record.side_effects.value,
        "idempotency": record.idempotency.value,
        "reconciliation_support": record.reconciliation_support.value,
        "idempotency_key": record.idempotency_key,
        "status": record.status.value,
        "disposition": record.disposition.value,
        "reason": record.reason,
        "dispatch_attempts": record.dispatch_attempts,
        "reconciliation_attempts": record.reconciliation_attempts,
        "duplicate_callbacks_ignored": record.duplicate_callbacks_ignored,
        "result_ref": record.result_ref,
        "artifact_refs": list(record.artifact_refs),
        "evidence_refs": list(record.evidence_refs),
        "adapter_metadata": _metadata_to_json(record.adapter_metadata),
        "last_operator_actor": record.last_operator_actor,
        "last_operator_reason": record.last_operator_reason,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_json(data: dict[str, JsonValue]) -> ExternalEffectRecoveryRecord:
    return ExternalEffectRecoveryRecord(
        effect_id=cast(str, data["effect_id"]),
        invocation_id=cast(str, data["invocation_id"]),
        canonical_tool_invocation_id=cast(
            str | None,
            data.get("canonical_tool_invocation_id"),
        ),
        task_id=cast(str, data["task_id"]),
        run_id=cast(str, data["run_id"]),
        capability_id=cast(str, data["capability_id"]),
        capability_version=cast(str, data["capability_version"]),
        provider_id=cast(str, data["provider_id"]),
        provider_tool_ref=cast(str, data["provider_tool_ref"]),
        side_effects=SideEffectClassification(cast(str, data["side_effects"])),
        idempotency=ExternalEffectIdempotency(cast(str, data["idempotency"])),
        reconciliation_support=ExternalEffectReconciliationSupport(
            cast(str, data["reconciliation_support"])
        ),
        idempotency_key=cast(str | None, data.get("idempotency_key")),
        status=ExternalEffectRecoveryStatus(cast(str, data["status"])),
        disposition=ExternalEffectRecoveryDisposition(cast(str, data["disposition"])),
        reason=cast(str, data["reason"]),
        dispatch_attempts=int(cast(int, data.get("dispatch_attempts", 1))),
        reconciliation_attempts=int(cast(int, data.get("reconciliation_attempts", 0))),
        duplicate_callbacks_ignored=int(
            cast(int, data.get("duplicate_callbacks_ignored", 0))
        ),
        result_ref=cast(str | None, data.get("result_ref")),
        artifact_refs=tuple(cast(list[str], data.get("artifact_refs", []))),
        evidence_refs=tuple(cast(list[str], data.get("evidence_refs", []))),
        adapter_metadata=_metadata_from_json(data.get("adapter_metadata", [])),
        last_operator_actor=cast(str | None, data.get("last_operator_actor")),
        last_operator_reason=cast(str | None, data.get("last_operator_reason")),
        created_at=datetime.fromisoformat(cast(str, data["created_at"])),
        updated_at=datetime.fromisoformat(cast(str, data["updated_at"])),
    )
