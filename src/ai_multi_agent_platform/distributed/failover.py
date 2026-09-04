"""Backend-neutral fencing contracts for controlled cross-Worker failover."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ai_multi_agent_platform.domain import validate_id

from .models import WorkerJobRequest, utc_now
from .registry import RegistryError


class FailoverRejectionCode(StrEnum):
    """Structured reasons why ownership transfer cannot proceed safely."""

    STATE_NOT_LOST = "state_not_lost"
    CANCELLATION_PENDING = "cancellation_pending"
    RETRY_FORBIDDEN = "retry_forbidden"
    FENCE_UNAVAILABLE = "fence_unavailable"
    FENCE_REJECTED = "fence_rejected"
    FENCE_IDENTITY_MISMATCH = "fence_identity_mismatch"
    NOT_FENCED = "not_fenced"
    NO_ALTERNATE_WORKER = "no_alternate_worker"


class FailoverError(RegistryError):
    """Raised when failover would violate retry or ownership safety."""

    def __init__(self, code: FailoverRejectionCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FailoverFenceReceipt:
    """Evidence from an external authority that one Worker no longer owns a job."""

    worker_job_id: str
    worker_id: str
    fence_ref: str
    fenced_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.worker_job_id, "worker_job")
        validate_id(self.worker_id, "worker")
        if not self.fence_ref.strip():
            raise ValueError("fence_ref must not be blank")


class WorkerOwnershipFencer(Protocol):
    """Replaceable authority able to prove a lost Worker execution is fenced/stopped."""

    async def fence(
        self,
        *,
        worker_id: str,
        job: WorkerJobRequest,
    ) -> FailoverFenceReceipt: ...
