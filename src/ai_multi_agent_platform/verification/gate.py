"""Canonical task-completion authority bridging Verification into the kernel."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .models import (
    CompletionState,
    ProducerIdentity,
    VerificationRequest,
    VerificationSubject,
)
from .service import _CANONICAL_SUBJECT_TOKEN, VerificationService


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class TaskVerificationRequirement:
    """Current versioned Verification requirement governing one Task completion."""

    task_id: str
    policy_id: str
    policy_version: int
    subject: VerificationSubject | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.policy_id, "verification_policy")
        if self.policy_version < 1:
            raise ValueError("verification requirement policy_version must be >= 1")
        _require_aware(self.created_at, "verification requirement created_at")
        _require_aware(self.updated_at, "verification requirement updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("verification requirement updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class CompletionGateDecision:
    """Kernel-facing deterministic decision; subject may be pending production/binding."""

    task_id: str
    state: CompletionState
    reason: str
    policy_id: str | None = None
    policy_version: int | None = None
    subject: VerificationSubject | None = None
    blocking_verification_ids: tuple[str, ...] = ()
    repair_attempts_remaining: int = 0

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        if not self.reason.strip():
            raise ValueError("completion gate decision reason must not be blank")
        if self.policy_id is not None:
            validate_id(self.policy_id, "verification_policy")
            if self.policy_version is None or self.policy_version < 1:
                raise ValueError("completion gate policy requires version >= 1")
        elif self.policy_version is not None:
            raise ValueError("completion gate policy_version requires policy_id")
        for verification_id in self.blocking_verification_ids:
            validate_id(verification_id, "verification")
        if self.repair_attempts_remaining < 0:
            raise ValueError("repair_attempts_remaining must be >= 0")


@runtime_checkable
class CompletionAuthority(Protocol):
    """Synchronous deterministic authority consulted at canonical Task terminalization."""

    def assess_task_completion(self, task_id: str) -> CompletionGateDecision: ...


class VerificationCompletionAuthority(CompletionAuthority):
    """Own Task→policy/subject binding while VerificationService owns review evidence."""

    def __init__(self, verification: VerificationService) -> None:
        self._verification = verification
        self._requirements: dict[str, TaskVerificationRequirement] = {}

    @property
    def verification(self) -> VerificationService:
        return self._verification

    def require_task(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement:
        validate_id(task_id, "task")
        self._verification.get_policy(policy_id, policy_version)
        current = _require_aware(now or _utc_now(), "verification requirement time")
        existing = self._requirements.get(task_id)
        if existing is not None:
            if (existing.policy_id, existing.policy_version) != (policy_id, policy_version):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "task already has a different canonical verification requirement",
                )
            return existing
        requirement = TaskVerificationRequirement(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            created_at=current,
            updated_at=current,
        )
        self._requirements[task_id] = requirement
        return requirement

    def requirement_for(self, task_id: str) -> TaskVerificationRequirement | None:
        validate_id(task_id, "task")
        return self._requirements.get(task_id)

    def bind_subject(
        self,
        *,
        task_id: str,
        subject: VerificationSubject,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement:
        validate_id(task_id, "task")
        try:
            requirement = self._requirements[task_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                "task has no canonical verification requirement",
            ) from exc
        current = _require_aware(now or _utc_now(), "verification subject binding time")
        if requirement.subject == subject:
            return requirement
        updated = replace(requirement, subject=subject, updated_at=current)
        self._requirements[task_id] = updated
        return updated

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
        self.require_task(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            now=now,
        )
        self.bind_subject(task_id=task_id, subject=subject, now=now)
        return request

    def request_canonical_verification(
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
        """Bind a subject that was resolved from canonical platform evidence."""

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
            _canonical_subject_token=_CANONICAL_SUBJECT_TOKEN,
        )
        self.require_task(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            now=now,
        )
        self.bind_subject(task_id=task_id, subject=subject, now=now)
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
        self.require_task(
            task_id=request.task_id,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
        )
        self.bind_subject(task_id=request.task_id, subject=request.subject)
        return request

    def request_canonical_reverification_after_repair(
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
        """Rebind a repaired subject and its newly derived producer context."""

        request = self._verification.request_reverification_after_repair(
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
            _replace_context=True,
            _canonical_subject_token=_CANONICAL_SUBJECT_TOKEN,
        )
        self.require_task(
            task_id=request.task_id,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
        )
        self.bind_subject(task_id=request.task_id, subject=request.subject)
        return request

    def assess_task_completion(self, task_id: str) -> CompletionGateDecision:
        validate_id(task_id, "task")
        requirement = self._requirements.get(task_id)
        if requirement is None:
            return CompletionGateDecision(
                task_id=task_id,
                state=CompletionState.ACCEPTED,
                reason="no verification requirement is registered for task",
            )
        policy = self._verification.get_policy(
            requirement.policy_id,
            requirement.policy_version,
        )
        if not policy.stages:
            return CompletionGateDecision(
                task_id=task_id,
                state=CompletionState.ACCEPTED,
                reason="verification policy contains no required stages",
                policy_id=requirement.policy_id,
                policy_version=requirement.policy_version,
                subject=requirement.subject,
            )
        if requirement.subject is None:
            return CompletionGateDecision(
                task_id=task_id,
                state=CompletionState.WAITING,
                reason="required verification subject has not been bound yet",
                policy_id=requirement.policy_id,
                policy_version=requirement.policy_version,
            )
        assessment = self._verification.assess_completion(
            task_id=task_id,
            subject=requirement.subject,
            policy_id=requirement.policy_id,
            policy_version=requirement.policy_version,
        )
        return CompletionGateDecision(
            task_id=task_id,
            state=assessment.state,
            reason=assessment.reason,
            policy_id=assessment.policy_id,
            policy_version=assessment.policy_version,
            subject=assessment.subject,
            blocking_verification_ids=assessment.blocking_verification_ids,
            repair_attempts_remaining=assessment.repair_attempts_remaining,
        )
