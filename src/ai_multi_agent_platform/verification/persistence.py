"""Restart-safe SQLite persistence for canonical runtime Verification state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Provenance

from .audit import VerificationAuditEvent, VerificationAuditEventType
from .gate import TaskVerificationRequirement, VerificationCompletionAuthority
from .models import (
    ProducerIdentity,
    ReviewerIndependence,
    VerificationError,
    VerificationFailurePolicy,
    VerificationFinding,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerificationScope,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from .service import VerificationService

VERIFICATION_PERSISTENCE_SCHEMA_VERSION = "1"


_DATACLASS_TYPES: dict[str, type[Any]] = {
    cls.__name__: cls
    for cls in (
        Provenance,
        VerificationAuditEvent,
        ProducerIdentity,
        ReviewerIndependence,
        TaskVerificationRequirement,
        VerificationError,
        VerificationFinding,
        VerificationPolicy,
        VerificationRequest,
        VerificationResult,
        VerificationScope,
        VerificationStage,
        VerificationSubject,
        VerifierIdentity,
    )
}
_ENUM_TYPES: dict[str, type[Enum]] = {
    cls.__name__: cls
    for cls in (
        VerificationAuditEventType,
        VerificationFailurePolicy,
        VerificationOutcome,
        VerificationRequestStatus,
        VerifierKind,
    )
}


class _SqliteVerificationState:
    """Small shared snapshot store used by the service and completion authority."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS verification_snapshots (
                        namespace TEXT PRIMARY KEY,
                        payload TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize verification persistence",
            ) from exc

    def _write_snapshot(self, namespace: str, document: Mapping[str, Any]) -> None:
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO verification_snapshots(namespace, payload)
                    VALUES (?, ?)
                    ON CONFLICT(namespace) DO UPDATE SET payload = excluded.payload
                    """,
                    (namespace, encoded),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist verification state",
            ) from exc

    def _read_snapshot(self, namespace: str) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM verification_snapshots WHERE namespace = ?",
                    (namespace,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read verification state",
            ) from exc
        if row is None:
            return None
        try:
            document = json.loads(cast(str, row["payload"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "persisted verification state is invalid JSON",
            ) from exc
        if not isinstance(document, dict):
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "persisted verification state must be an object",
            )
        return cast(dict[str, Any], document)

    @staticmethod
    def _require_schema(document: Mapping[str, Any]) -> None:
        if document.get("schema_version") != VERIFICATION_PERSISTENCE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "unsupported persisted verification schema version",
            )


class SqliteVerificationService(_SqliteVerificationState, VerificationService):
    """Canonical VerificationService whose policy/request/result state survives restart."""

    _NAMESPACE = "verification-service"

    def __init__(self, db_path: str | Path) -> None:
        VerificationService.__init__(self)
        _SqliteVerificationState.__init__(self, db_path)
        self._restore_service_state()

    def register_policy(self, policy: VerificationPolicy) -> VerificationPolicy:
        registered = super().register_policy(policy)
        self._save_service_state()
        return registered

    def request_verification(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: ProducerIdentity | None = None,
        repair_attempt: int = 0,
        causation_id: str | None = None,
        now: datetime | None = None,
    ) -> VerificationRequest:
        request = super().request_verification(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id,
            capability_ids=capability_ids,
            producer=producer,
            repair_attempt=repair_attempt,
            causation_id=causation_id,
            now=now,
        )
        self._save_service_state()
        return request

    def get_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
    ) -> VerificationRequest:
        before = self._requests.get(verification_id)
        request = super().get_request(verification_id, now=now)
        if before is not None and before != request:
            self._save_service_state()
        return request

    def submit_result(self, result: VerificationResult) -> VerificationResult:
        submitted = super().submit_result(result)
        self._save_service_state()
        return submitted

    def cancel_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        cancelled = super().cancel_request(
            verification_id,
            now=now,
            causation_id=causation_id,
        )
        self._save_service_state()
        return cancelled

    def snapshot_requests(self) -> tuple[VerificationRequest, ...]:
        """Return durable request creation order for recovery reconciliation."""

        return tuple(self._requests.values())

    def _save_service_state(self) -> None:
        document: dict[str, Any] = {
            "schema_version": VERIFICATION_PERSISTENCE_SCHEMA_VERSION,
            "policies": [
                _encode(policy)
                for _key, policy in sorted(
                    self._policies.items(),
                    key=lambda item: (item[0][0], item[0][1]),
                )
            ],
            "requests": [_encode(request) for request in self._requests.values()],
            "results": [
                _encode(result)
                for result in sorted(
                    self._results.values(),
                    key=lambda item: item.verification_result_id,
                )
            ],
            "audit_events": [_encode(event) for event in self._audit_events],
        }
        self._write_snapshot(self._NAMESPACE, document)

    def _restore_service_state(self) -> None:
        document = self._read_snapshot(self._NAMESPACE)
        if document is None:
            return
        self._require_schema(document)
        policies = _decode_sequence(document, "policies", VerificationPolicy)
        requests = _decode_sequence(document, "requests", VerificationRequest)
        results = _decode_sequence(document, "results", VerificationResult)
        audit_events = (
            ()
            if "audit_events" not in document
            else _decode_sequence(document, "audit_events", VerificationAuditEvent)
        )

        restored_policies: dict[tuple[str, int], VerificationPolicy] = {}
        for policy in policies:
            key = (policy.policy_id, policy.version)
            if key in restored_policies:
                raise _corrupt("duplicate persisted verification policy version")
            restored_policies[key] = policy

        restored_requests: dict[str, VerificationRequest] = {}
        for request in requests:
            if request.verification_id in restored_requests:
                raise _corrupt("duplicate persisted verification request")
            restored_policy = restored_policies.get((request.policy_id, request.policy_version))
            if restored_policy is None:
                raise _corrupt("persisted verification request references missing policy")
            try:
                stage = restored_policy.stage(request.stage_id)
            except KeyError as exc:
                raise _corrupt("persisted verification request references missing stage") from exc
            if stage.verifier_kind is not request.requested_verifier_kind:
                raise _corrupt("persisted verification request verifier kind differs from policy")
            restored_requests[request.verification_id] = request

        restored_results: dict[str, VerificationResult] = {}
        result_by_verification: dict[str, str] = {}
        for result in results:
            if result.verification_result_id in restored_results:
                raise _corrupt("duplicate persisted verification result")
            if result.verification_id in result_by_verification:
                raise _corrupt("multiple persisted results exist for one verification request")
            restored_request = restored_requests.get(result.verification_id)
            if restored_request is None:
                raise _corrupt("persisted verification result references missing request")
            if result.subject != restored_request.subject:
                raise _corrupt("persisted verification result subject differs from request")
            if result.verifier.kind is not restored_request.requested_verifier_kind:
                raise _corrupt("persisted verification result verifier kind differs from request")
            restored_results[result.verification_result_id] = result
            result_by_verification[result.verification_id] = result.verification_result_id

        for request in restored_requests.values():
            has_result = request.verification_id in result_by_verification
            if request.status is VerificationRequestStatus.COMPLETED and not has_result:
                raise _corrupt("completed persisted verification request has no result")
            if request.status is not VerificationRequestStatus.COMPLETED and has_result:
                raise _corrupt("non-completed persisted verification request already has a result")

        seen_audit_ids: set[str] = set()
        for event in audit_events:
            if event.event_id in seen_audit_ids:
                raise _corrupt("duplicate persisted verification audit event")
            seen_audit_ids.add(event.event_id)
            if event.policy_id is not None:
                assert event.policy_version is not None
                if (event.policy_id, event.policy_version) not in restored_policies:
                    raise _corrupt("persisted verification audit references missing policy")
            if event.verification_id is None:
                continue
            restored_request = restored_requests.get(event.verification_id)
            if restored_request is None:
                raise _corrupt("persisted verification audit references missing request")
            if event.task_id != restored_request.task_id:
                raise _corrupt("persisted verification audit task differs from request")
            if event.subject is not None and event.subject != restored_request.subject:
                raise _corrupt("persisted verification audit subject differs from request")
            if event.event_type is VerificationAuditEventType.RESULT_RECORDED:
                result_id = result_by_verification.get(event.verification_id)
                if result_id is None:
                    raise _corrupt("persisted result audit has no canonical result")
                restored_result = restored_results[result_id]
                if event.outcome is not restored_result.outcome:
                    raise _corrupt("persisted result audit outcome differs from result")
                if event.verifier != restored_result.verifier:
                    raise _corrupt("persisted result audit verifier differs from result")

        self._policies = restored_policies
        self._requests = restored_requests
        self._results = restored_results
        self._result_by_verification = result_by_verification
        self._audit_events = list(audit_events)


class SqliteVerificationCompletionAuthority(
    _SqliteVerificationState,
    VerificationCompletionAuthority,
):
    """Restart-safe Task→verification policy/subject completion binding."""

    _NAMESPACE = "verification-requirements"

    def __init__(
        self,
        verification: VerificationService,
        db_path: str | Path,
    ) -> None:
        VerificationCompletionAuthority.__init__(self, verification)
        _SqliteVerificationState.__init__(self, db_path)
        self._restore_requirements()

    def request_verification(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: ProducerIdentity | None = None,
        repair_attempt: int = 0,
        causation_id: str | None = None,
        now: datetime | None = None,
    ) -> VerificationRequest:
        request = self._verification.request_verification(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id,
            capability_ids=capability_ids,
            producer=producer,
            repair_attempt=repair_attempt,
            causation_id=causation_id,
            now=now,
        )
        VerificationCompletionAuthority.require_task(
            self,
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            now=now,
        )
        VerificationCompletionAuthority.bind_subject(
            self,
            task_id=task_id,
            subject=subject,
            now=now,
        )
        self._save_requirements()
        return request

    def request_reverification_after_repair(
        self,
        verification_id: str,
        *,
        new_subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        causation_id: str | None = None,
    ) -> VerificationRequest:
        request = self._verification.request_reverification_after_repair(
            verification_id,
            new_subject=new_subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            causation_id=causation_id,
        )
        VerificationCompletionAuthority.require_task(
            self,
            task_id=request.task_id,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
        )
        VerificationCompletionAuthority.bind_subject(
            self,
            task_id=request.task_id,
            subject=request.subject,
        )
        self._save_requirements()
        return request

    def require_task(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement:
        requirement = super().require_task(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            now=now,
        )
        self._save_requirements()
        return requirement

    def bind_subject(
        self,
        *,
        task_id: str,
        subject: VerificationSubject,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement:
        requirement = super().bind_subject(task_id=task_id, subject=subject, now=now)
        self._save_requirements()
        return requirement

    def _save_requirements(self) -> None:
        request_count: int | None = None
        if isinstance(self._verification, SqliteVerificationService):
            request_count = len(self._verification.snapshot_requests())
        document: dict[str, Any] = {
            "schema_version": VERIFICATION_PERSISTENCE_SCHEMA_VERSION,
            "service_request_count": request_count,
            "requirements": [
                _encode(requirement)
                for requirement in sorted(
                    self._requirements.values(),
                    key=lambda item: item.task_id,
                )
            ],
        }
        self._write_snapshot(self._NAMESPACE, document)

    def _restore_requirements(self) -> None:
        document = self._read_snapshot(self._NAMESPACE)
        restored: dict[str, TaskVerificationRequirement] = {}
        persisted_request_count = 0
        if document is not None:
            self._require_schema(document)
            raw_count = document.get("service_request_count", 0)
            if raw_count is not None and (
                not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 0
            ):
                raise _corrupt("persisted verification request count is invalid")
            persisted_request_count = 0 if raw_count is None else raw_count
            requirements = _decode_sequence(
                document,
                "requirements",
                TaskVerificationRequirement,
            )
            for requirement in requirements:
                if requirement.task_id in restored:
                    raise _corrupt("duplicate persisted task verification requirement")
                self._verification.get_policy(
                    requirement.policy_id,
                    requirement.policy_version,
                )
                restored[requirement.task_id] = requirement

        self._requirements = restored
        if not isinstance(self._verification, SqliteVerificationService):
            return

        requests = self._verification.snapshot_requests()
        if persisted_request_count > len(requests):
            raise _corrupt("persisted verification requirement state is ahead of service state")
        if persisted_request_count == len(requests):
            return

        for request in requests[persisted_request_count:]:
            existing = self._requirements.get(request.task_id)
            if existing is None:
                self._requirements[request.task_id] = TaskVerificationRequirement(
                    task_id=request.task_id,
                    policy_id=request.policy_id,
                    policy_version=request.policy_version,
                    subject=request.subject,
                    created_at=request.created_at,
                    updated_at=request.created_at,
                )
                continue
            if (existing.policy_id, existing.policy_version) != (
                request.policy_id,
                request.policy_version,
            ):
                raise _corrupt("newer persisted verification request conflicts with task policy")
            if existing.subject != request.subject:
                self._requirements[request.task_id] = replace(
                    existing,
                    subject=request.subject,
                    updated_at=max(existing.updated_at, request.created_at),
                )

        self._save_requirements()


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"$kind": "enum", "type": type(value).__name__, "value": value.value}
    if isinstance(value, datetime):
        return {"$kind": "datetime", "value": value.isoformat()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$kind": "dataclass",
            "type": type(value).__name__,
            "fields": {item.name: _encode(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, Mapping):
        return {
            "$kind": "mapping",
            "items": [[str(key), _encode(item)] for key, item in value.items()],
        }
    if isinstance(value, tuple):
        return {"$kind": "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, list):
        return {"$kind": "list", "items": [_encode(item) for item in value]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"verification persistence cannot encode {type(value).__name__}")


def _decode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if not isinstance(value, dict):
        raise _corrupt("persisted verification value has invalid structure")
    kind = value.get("$kind")
    if kind == "datetime":
        raw = value.get("value")
        if not isinstance(raw, str):
            raise _corrupt("persisted verification datetime is invalid")
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise _corrupt("persisted verification datetime cannot be parsed") from exc
    if kind == "enum":
        type_name, raw = value.get("type"), value.get("value")
        enum_type = _ENUM_TYPES.get(type_name) if isinstance(type_name, str) else None
        if enum_type is None:
            raise _corrupt("persisted verification enum type is unsupported")
        try:
            return enum_type(raw)
        except (TypeError, ValueError) as exc:
            raise _corrupt("persisted verification enum value is invalid") from exc
    if kind in {"tuple", "list"}:
        raw_items = value.get("items")
        if not isinstance(raw_items, list):
            raise _corrupt("persisted verification sequence is invalid")
        decoded = [_decode(item) for item in raw_items]
        return tuple(decoded) if kind == "tuple" else decoded
    if kind == "mapping":
        raw_items = value.get("items")
        if not isinstance(raw_items, list):
            raise _corrupt("persisted verification mapping is invalid")
        decoded_mapping: dict[str, Any] = {}
        for pair in raw_items:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str):
                raise _corrupt("persisted verification mapping entry is invalid")
            decoded_mapping[pair[0]] = _decode(pair[1])
        return decoded_mapping
    if kind == "dataclass":
        type_name, raw_fields = value.get("type"), value.get("fields")
        data_type = _DATACLASS_TYPES.get(type_name) if isinstance(type_name, str) else None
        if data_type is None or not isinstance(raw_fields, dict):
            raise _corrupt("persisted verification dataclass is unsupported")
        decoded_fields = {str(key): _decode(item) for key, item in raw_fields.items()}
        try:
            return data_type(**decoded_fields)
        except (TypeError, ValueError) as exc:
            raise _corrupt("persisted verification dataclass failed validation") from exc
    raise _corrupt("persisted verification value has unknown type tag")


def _decode_sequence[T](
    document: Mapping[str, Any],
    field_name: str,
    expected_type: type[T],
) -> tuple[T, ...]:
    raw = document.get(field_name)
    if not isinstance(raw, list):
        raise _corrupt(f"persisted verification field {field_name!r} must be an array")
    decoded: list[T] = []
    for item in raw:
        value = _decode(item)
        if not isinstance(value, expected_type):
            raise _corrupt(
                f"persisted verification field {field_name!r} contains wrong record type"
            )
        decoded.append(value)
    return tuple(decoded)


def _corrupt(message: str) -> ContractError:
    return ContractError(ErrorCode.BACKEND_ERROR, message)
