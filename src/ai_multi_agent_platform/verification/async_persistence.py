"""Awaitable Verification persistence boundary for blocking runtime backends."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .audit import VerificationAuditEvent
from .canonical_access import CanonicalVerificationAccess
from .gate import (
    CompletionGateDecision,
    TaskVerificationRequirement,
    VerificationCompletionAuthority,
)
from .models import (
    ProducerIdentity,
    VerificationPolicy,
    VerificationRequest,
    VerificationResult,
    VerificationSubject,
    VerifierIdentity,
)
from .service import VerificationService

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncVerificationService(Protocol):
    """Backend-neutral awaitable Verification service contract for runtime callers."""

    async def get_policy(self, policy_id: str, version: int) -> VerificationPolicy: ...

    async def get_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
    ) -> VerificationRequest: ...

    async def result_for(self, verification_id: str) -> VerificationResult | None: ...

    async def history(
        self,
        *,
        task_id: str,
    ) -> tuple[tuple[VerificationRequest, VerificationResult | None], ...]: ...

    async def audit_history(
        self,
        *,
        task_id: str | None = None,
        verification_id: str | None = None,
    ) -> tuple[VerificationAuditEvent, ...]: ...

    async def validate_verifier(
        self,
        verification_id: str,
        verifier: VerifierIdentity,
    ) -> VerifierIdentity: ...

    async def cancel_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest: ...

    async def submit_result(self, result: VerificationResult) -> VerificationResult: ...

    async def submit_canonical_result(self, result: VerificationResult) -> VerificationResult: ...


class AsyncVerificationCompletionAuthority(Protocol):
    """Awaitable runtime contract for persistence-bearing completion authority operations."""

    async def require_task(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement: ...

    async def requirement_for(self, task_id: str) -> TaskVerificationRequirement | None: ...

    async def request_canonical_verification(
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
    ) -> VerificationRequest: ...

    async def request_canonical_reverification_after_repair(
        self,
        verification_id: str,
        *,
        new_subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: ProducerIdentity | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest: ...

    async def assess_task_completion(self, task_id: str) -> CompletionGateDecision: ...

    async def invalidate_task_subject(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement | None: ...


class VerificationPersistenceOffload:
    """Serialize and bound Verification persistence outside the asyncio event loop."""

    def __init__(self, *, max_concurrency: int = 2, max_pending: int = 64) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="verification-persistence",
        )
        self._store_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending

    async def run[T](self, operation: Callable[[], T]) -> T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "Verification persistence queue capacity exhausted",
                retryable=True,
            )
        loop = asyncio.get_running_loop()
        try:
            worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        except BaseException:
            self._capacity.release()
            raise
        return await _await_persistence_boundary(worker)

    def _run_sync[T](self, operation: Callable[[], T]) -> T:
        try:
            with self._store_lock:
                return operation()
        finally:
            self._capacity.release()


_SHARED_VERIFICATION_OFFLOADS = SharedPersistenceOffloadRegistry[VerificationPersistenceOffload]()


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        failure = worker.exception()
        if failure is not None:
            raise failure from None
        raise


def _sqlite_error(exc: BaseException) -> sqlite3.Error | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, sqlite3.Error):
            return current
        current = current.__cause__
    return None


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True)
    return ContractError(ErrorCode.BACKEND_ERROR, message)


class AsyncVerificationServiceAdapter:
    """Awaitable facade over the canonical synchronous Verification service."""

    def __init__(
        self,
        service: VerificationService,
        *,
        offload: VerificationPersistenceOffload | None = None,
    ) -> None:
        self._service = service
        self._canonical = CanonicalVerificationAccess(service)
        self._offload = _SHARED_VERIFICATION_OFFLOADS.resolve(
            service,
            owner=self,
            requested=offload,
            factory=VerificationPersistenceOffload,
        )

    @property
    def offload(self) -> VerificationPersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError as exc:
            sqlite_error = _sqlite_error(exc)
            if sqlite_error is None:
                raise
            raise _map_sqlite_error(sqlite_error, message) from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def get_policy(self, policy_id: str, version: int) -> VerificationPolicy:
        return await self._run(
            lambda: self._service.get_policy(policy_id, version),
            message="failed to read Verification policy",
        )

    async def get_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
    ) -> VerificationRequest:
        return await self._run(
            lambda: self._service.get_request(verification_id, now=now),
            message="failed to read Verification request",
        )

    async def result_for(self, verification_id: str) -> VerificationResult | None:
        return await self._run(
            lambda: self._service.result_for(verification_id),
            message="failed to read Verification result",
        )

    async def history(
        self,
        *,
        task_id: str,
    ) -> tuple[tuple[VerificationRequest, VerificationResult | None], ...]:
        return await self._run(
            lambda: self._service.history(task_id=task_id),
            message="failed to read Verification history",
        )

    async def audit_history(
        self,
        *,
        task_id: str | None = None,
        verification_id: str | None = None,
    ) -> tuple[VerificationAuditEvent, ...]:
        return await self._run(
            lambda: self._service.audit_history(
                task_id=task_id,
                verification_id=verification_id,
            ),
            message="failed to read Verification audit history",
        )

    async def validate_verifier(
        self,
        verification_id: str,
        verifier: VerifierIdentity,
    ) -> VerifierIdentity:
        return await self._run(
            lambda: self._service.validate_verifier(verification_id, verifier),
            message="failed to validate Verification verifier",
        )

    async def cancel_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        return await self._run(
            lambda: self._service.cancel_request(
                verification_id,
                now=now,
                causation_id=causation_id,
            ),
            message="failed to cancel Verification request",
        )

    async def submit_result(self, result: VerificationResult) -> VerificationResult:
        return await self._run(
            lambda: self._service.submit_result(result),
            message="failed to persist Verification result",
        )

    async def submit_canonical_result(self, result: VerificationResult) -> VerificationResult:
        return await self._run(
            lambda: self._canonical.submit_result(result),
            message="failed to persist Verification result",
        )


class AsyncVerificationCompletionAuthorityAdapter:
    """Awaitable facade sharing the Verification service's persistence serialization boundary."""

    def __init__(
        self,
        completion: VerificationCompletionAuthority,
        *,
        offload: VerificationPersistenceOffload | None = None,
    ) -> None:
        self._completion = completion
        self._offload = _SHARED_VERIFICATION_OFFLOADS.resolve(
            completion.verification,
            owner=self,
            requested=offload,
            factory=VerificationPersistenceOffload,
        )

    @property
    def offload(self) -> VerificationPersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError as exc:
            sqlite_error = _sqlite_error(exc)
            if sqlite_error is None:
                raise
            raise _map_sqlite_error(sqlite_error, message) from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def require_task(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement:
        return await self._run(
            lambda: self._completion.require_task(
                task_id=task_id,
                policy_id=policy_id,
                policy_version=policy_version,
                now=now,
            ),
            message="failed to persist Verification requirement",
        )

    async def requirement_for(self, task_id: str) -> TaskVerificationRequirement | None:
        return await self._run(
            lambda: self._completion.requirement_for(task_id),
            message="failed to read Verification requirement",
        )

    async def request_canonical_verification(
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
        return await self._run(
            lambda: self._completion.request_canonical_verification(
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
            ),
            message="failed to persist Verification request",
        )

    async def request_canonical_reverification_after_repair(
        self,
        verification_id: str,
        *,
        new_subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: ProducerIdentity | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        def request_or_reuse() -> VerificationRequest:
            previous = self._completion.verification.get_request(verification_id)
            next_attempt = previous.repair_attempt + 1
            candidates = tuple(
                request
                for request, _result in self._completion.verification.history(
                    task_id=previous.task_id
                )
                if request.verification_id != previous.verification_id
                and request.policy_id == previous.policy_id
                and request.policy_version == previous.policy_version
                and request.stage_id == previous.stage_id
                and request.repair_attempt == next_attempt
            )
            exact = tuple(
                request
                for request in candidates
                if request.requested_verifier_kind is previous.requested_verifier_kind
                and request.subject == new_subject
                and request.run_id == run_id
                and request.result_id == result_id
                and request.artifact_ids == artifact_ids
                and request.project_id == project_id
                and request.capability_ids == capability_ids
                and request.producer == producer
                and request.correlation_id == correlation_id
                and request.causation_id == causation_id
            )
            if len(exact) == 1:
                return exact[0]
            if len(exact) > 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repair output maps to multiple canonical reverification requests",
                    details={
                        "source_verification_id": previous.verification_id,
                        "verification_ids": [request.verification_id for request in exact],
                    },
                )
            lineage_conflicts = tuple(
                request
                for request in candidates
                if request.run_id == run_id
                or (causation_id is not None and request.causation_id == causation_id)
            )
            if lineage_conflicts:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "persisted repair reverification conflicts with current canonical evidence",
                    details={
                        "source_verification_id": previous.verification_id,
                        "verification_ids": [
                            request.verification_id for request in lineage_conflicts
                        ],
                        "repair_attempt": next_attempt,
                        "subject_id": new_subject.subject_id,
                        "run_id": run_id,
                    },
                )
            return self._completion.request_canonical_reverification_after_repair(
                verification_id,
                new_subject=new_subject,
                correlation_id=correlation_id,
                run_id=run_id,
                result_id=result_id,
                artifact_ids=artifact_ids,
                project_id=project_id,
                capability_ids=capability_ids,
                producer=producer,
                causation_id=causation_id,
            )

        return await self._run(
            request_or_reuse,
            message="failed to persist repaired Verification request",
        )

    async def assess_task_completion(self, task_id: str) -> CompletionGateDecision:
        return await self._run(
            lambda: self._completion.assess_task_completion(task_id),
            message="failed to assess Verification completion",
        )

    async def invalidate_task_subject(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement | None:
        return await self._run(
            lambda: self._completion.invalidate_task_subject(task_id, now=now),
            message="failed to persist Verification subject invalidation",
        )


def runtime_verification_service(
    service: VerificationService,
    *,
    runtime_service: AsyncVerificationService | None = None,
) -> AsyncVerificationService:
    if runtime_service is not None:
        return runtime_service
    return AsyncVerificationServiceAdapter(service)


def runtime_verification_completion(
    completion: VerificationCompletionAuthority,
    *,
    runtime_completion: AsyncVerificationCompletionAuthority | None = None,
) -> AsyncVerificationCompletionAuthority:
    if runtime_completion is not None:
        return runtime_completion
    return AsyncVerificationCompletionAuthorityAdapter(completion)


__all__ = [
    "AsyncVerificationCompletionAuthority",
    "AsyncVerificationCompletionAuthorityAdapter",
    "AsyncVerificationService",
    "AsyncVerificationServiceAdapter",
    "VerificationPersistenceOffload",
    "runtime_verification_completion",
    "runtime_verification_service",
]
