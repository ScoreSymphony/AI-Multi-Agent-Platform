"""Awaitable canonical Verification request access for async platform domains."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .async_persistence import AsyncVerificationService, AsyncVerificationServiceAdapter
from .models import ProducerIdentity, VerificationRequest, VerificationSubject
from .service import VerificationService


class AsyncCanonicalVerificationService(AsyncVerificationService, Protocol):
    """Awaitable Verification contract including canonical platform-owned request creation."""

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


class AsyncCanonicalVerificationServiceAdapter(AsyncVerificationServiceAdapter):
    """Extend the shared Verification offload with canonical request creation."""

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
            lambda: self._canonical.request_verification(
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
            message="failed to persist canonical Verification request",
        )


def runtime_canonical_verification_service(
    service: VerificationService,
    *,
    runtime_service: AsyncCanonicalVerificationService | None = None,
) -> AsyncCanonicalVerificationService:
    if runtime_service is not None:
        return runtime_service
    return AsyncCanonicalVerificationServiceAdapter(service)


__all__ = [
    "AsyncCanonicalVerificationService",
    "AsyncCanonicalVerificationServiceAdapter",
    "runtime_canonical_verification_service",
]
