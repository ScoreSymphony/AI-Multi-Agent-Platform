"""Platform-owned runtime Verification service and completion-policy evaluator."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .audit import VerificationAuditEvent, VerificationAuditEventType
from .deterministic import DeterministicVerifier
from .models import (
    CompletionAssessment,
    CompletionState,
    ProducerIdentity,
    VerificationFailurePolicy,
    VerificationFinding,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)

_CANONICAL_SUBJECT_TOKEN = object()
_CANONICAL_RESULT_TOKEN = object()


class VerificationService:
    """Reference canonical verification authority.

    Orchestrators and reviewer agents may ask this service to create requests or submit
    evidence, but only this service evaluates the versioned policy and exact subject
    binding. It deliberately exposes no method that directly mutates Task lifecycle
    state; kernel integration consumes ``assess_completion`` as a deterministic gate.
    """

    def __init__(
        self,
        *,
        require_canonical_subjects: bool = False,
        require_canonical_results: bool = False,
    ) -> None:
        self._require_canonical_subjects = require_canonical_subjects
        self._require_canonical_results = require_canonical_results
        self._policies: dict[tuple[str, int], VerificationPolicy] = {}
        self._requests: dict[str, VerificationRequest] = {}
        self._results: dict[str, VerificationResult] = {}
        self._result_by_verification: dict[str, str] = {}
        self._audit_events: list[VerificationAuditEvent] = []

    def register_policy(self, policy: VerificationPolicy) -> VerificationPolicy:
        key = (policy.policy_id, policy.version)
        existing = self._policies.get(key)
        if existing is not None and existing != policy:
            raise ContractError(
                ErrorCode.CONFLICT,
                "verification policy version already exists with different content",
            )
        self._policies[key] = policy
        if existing is None:
            self._audit_events.append(
                VerificationAuditEvent(
                    event_type=VerificationAuditEventType.POLICY_REGISTERED,
                    occurred_at=policy.created_at,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    metadata={
                        "creator_ref": policy.creator_ref,
                        "provenance_source": (
                            None if policy.provenance is None else policy.provenance.source
                        ),
                    },
                )
            )
        return policy

    def get_policy(self, policy_id: str, version: int) -> VerificationPolicy:
        try:
            return self._policies[(policy_id, version)]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "verification policy was not found") from exc

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
        _canonical_subject_token: object | None = None,
    ) -> VerificationRequest:
        if (
            self._require_canonical_subjects
            and _canonical_subject_token is not _CANONICAL_SUBJECT_TOKEN
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "raw verification subjects are disabled; use CanonicalVerificationRuntime",
            )
        policy = self.get_policy(policy_id, policy_version)
        try:
            stage = policy.stage(stage_id)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "verification stage was not found",
            ) from exc
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("verification request time must be timezone-aware")
        if repair_attempt > policy.max_repair_attempts:
            raise ContractError(ErrorCode.CONFLICT, "verification repair limit exhausted")
        self._require_policy_scope(
            policy,
            task_id=task_id,
            project_id=project_id,
            capability_ids=capability_ids,
            producer=producer,
        )
        expires_at = None
        if policy.request_timeout_seconds is not None:
            expires_at = current + timedelta(seconds=policy.request_timeout_seconds)
        request = VerificationRequest(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id=stage.stage_id,
            subject=subject,
            requested_verifier_kind=stage.verifier_kind,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id,
            capability_ids=capability_ids,
            requested_capability_ref=stage.capability_ref,
            producer=producer,
            repair_attempt=repair_attempt,
            created_at=current,
            expires_at=expires_at,
            causation_id=causation_id,
        )
        self._requests[request.verification_id] = request
        self._audit_events.append(
            VerificationAuditEvent(
                event_type=(
                    VerificationAuditEventType.REVERIFICATION_REQUESTED
                    if request.repair_attempt > 0
                    else VerificationAuditEventType.REQUESTED
                ),
                occurred_at=request.created_at,
                task_id=request.task_id,
                verification_id=request.verification_id,
                run_id=request.run_id,
                project_id=request.project_id,
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                stage_id=request.stage_id,
                subject=request.subject,
                requested_verifier_kind=request.requested_verifier_kind,
                repair_attempt=request.repair_attempt,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                metadata={
                    "result_id": request.result_id,
                    "artifact_ids": list(request.artifact_ids),
                    "capability_ids": list(request.capability_ids),
                    "requested_capability_ref": request.requested_capability_ref,
                },
            )
        )
        return request

    def get_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
    ) -> VerificationRequest:
        try:
            request = self._requests[verification_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "verification request was not found") from exc
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("verification lookup time must be timezone-aware")
        if (
            request.status is VerificationRequestStatus.PENDING
            and request.expires_at is not None
            and request.expires_at <= current
        ):
            request = replace(request, status=VerificationRequestStatus.EXPIRED)
            self._requests[verification_id] = request
            self._audit_events.append(
                VerificationAuditEvent(
                    event_type=VerificationAuditEventType.REQUEST_EXPIRED,
                    occurred_at=current,
                    task_id=request.task_id,
                    verification_id=request.verification_id,
                    run_id=request.run_id,
                    project_id=request.project_id,
                    policy_id=request.policy_id,
                    policy_version=request.policy_version,
                    stage_id=request.stage_id,
                    subject=request.subject,
                    requested_verifier_kind=request.requested_verifier_kind,
                    repair_attempt=request.repair_attempt,
                    correlation_id=request.correlation_id,
                    causation_id=request.causation_id,
                )
            )
        return request

    def cancel_request(
        self,
        verification_id: str,
        *,
        now: datetime | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        """Cancel one still-pending canonical verification obligation."""

        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("verification cancellation time must be timezone-aware")
        request = self.get_request(verification_id, now=current)
        if request.status is VerificationRequestStatus.CANCELLED:
            return request
        if request.status is not VerificationRequestStatus.PENDING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"verification request cannot be cancelled from {request.status.value}",
            )
        cancelled = replace(request, status=VerificationRequestStatus.CANCELLED)
        self._requests[verification_id] = cancelled
        self._audit_events.append(
            VerificationAuditEvent(
                event_type=VerificationAuditEventType.REQUEST_CANCELLED,
                occurred_at=current,
                task_id=cancelled.task_id,
                verification_id=cancelled.verification_id,
                run_id=cancelled.run_id,
                project_id=cancelled.project_id,
                policy_id=cancelled.policy_id,
                policy_version=cancelled.policy_version,
                stage_id=cancelled.stage_id,
                subject=cancelled.subject,
                requested_verifier_kind=cancelled.requested_verifier_kind,
                repair_attempt=cancelled.repair_attempt,
                correlation_id=cancelled.correlation_id,
                causation_id=causation_id or cancelled.causation_id,
            )
        )
        return cancelled

    def result_for(self, verification_id: str) -> VerificationResult | None:
        result_id = self._result_by_verification.get(verification_id)
        return None if result_id is None else self._results[result_id]

    def validate_verifier(
        self,
        verification_id: str,
        verifier: VerifierIdentity,
    ) -> VerifierIdentity:
        """Preflight one verifier identity against the exact pending request/policy."""

        request = self.get_request(verification_id)
        if request.status is not VerificationRequestStatus.PENDING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"verification request is not pending: {request.status.value}",
            )
        if verifier.kind is not request.requested_verifier_kind:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verifier kind differs from the requested verification stage",
            )
        policy = self.get_policy(request.policy_id, request.policy_version)
        self._enforce_independence(policy, request, verifier)
        return verifier

    def submit_result(
        self,
        result: VerificationResult,
        *,
        _canonical_result_token: object | None = None,
    ) -> VerificationResult:
        if (
            self._require_canonical_results
            and _canonical_result_token is not _CANONICAL_RESULT_TOKEN
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "raw verification results are disabled; use CanonicalVerificationRuntime",
            )
        request = self.get_request(result.verification_id)
        if request.status is not VerificationRequestStatus.PENDING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"verification request is not pending: {request.status.value}",
            )
        if result.subject != request.subject:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification result does not match the exact requested subject revision/digest",
            )
        if result.verifier.kind is not request.requested_verifier_kind:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification result uses a verifier kind different from the request",
            )
        policy = self.get_policy(request.policy_id, request.policy_version)
        self._enforce_independence(policy, request, result.verifier)
        self._results[result.verification_result_id] = result
        self._result_by_verification[request.verification_id] = result.verification_result_id
        self._requests[request.verification_id] = replace(
            request,
            status=VerificationRequestStatus.COMPLETED,
        )
        self._audit_events.append(
            VerificationAuditEvent(
                event_type=VerificationAuditEventType.RESULT_RECORDED,
                occurred_at=result.completed_at,
                task_id=request.task_id,
                verification_id=request.verification_id,
                run_id=request.run_id,
                project_id=request.project_id,
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                stage_id=request.stage_id,
                subject=result.subject,
                requested_verifier_kind=request.requested_verifier_kind,
                verifier=result.verifier,
                outcome=result.outcome,
                repair_attempt=request.repair_attempt,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                evidence_artifact_ids=result.evidence_artifact_ids,
                checks_executed=result.checks_executed,
                metadata={
                    "finding_count": len(result.findings),
                    "error_codes": [error.code for error in result.errors],
                },
            )
        )
        return result

    def run_deterministic(
        self,
        verification_id: str,
        verifier: DeterministicVerifier,
    ) -> VerificationResult:
        request = self.get_request(verification_id)
        return self.submit_result(verifier.verify(request))

    def record_human_review(
        self,
        verification_id: str,
        *,
        reviewer_ref: str,
        outcome: VerificationOutcome,
        comment: str | None = None,
        evidence_artifact_ids: tuple[str, ...] = (),
    ) -> VerificationResult:
        request = self.get_request(verification_id)
        findings: tuple[VerificationFinding, ...] = ()
        if comment is not None:
            if not comment.strip():
                raise ValueError("human review comment must not be blank")
            findings = (
                VerificationFinding(
                    code="human_review_comment",
                    message=comment,
                    severity="info" if outcome is VerificationOutcome.PASS else "warning",
                ),
            )
        return self.submit_result(
            VerificationResult(
                verification_id=verification_id,
                verifier=VerifierIdentity(
                    verifier_ref=reviewer_ref,
                    kind=VerifierKind.HUMAN,
                    read_only=True,
                ),
                outcome=outcome,
                subject=request.subject,
                findings=findings,
                evidence_artifact_ids=evidence_artifact_ids,
                checks_executed=("human_review",),
            )
        )

    def record_agent_review(
        self,
        verification_id: str,
        *,
        verifier: VerifierIdentity,
        outcome: VerificationOutcome,
        findings: tuple[VerificationFinding, ...] = (),
        evidence_artifact_ids: tuple[str, ...] = (),
        checks_executed: tuple[str, ...] = ("agent_review",),
    ) -> VerificationResult:
        if verifier.kind is not VerifierKind.AGENT:
            raise ValueError("record_agent_review requires an agent verifier identity")
        request = self.get_request(verification_id)
        return self.submit_result(
            VerificationResult(
                verification_id=verification_id,
                verifier=verifier,
                outcome=outcome,
                subject=request.subject,
                findings=findings,
                evidence_artifact_ids=evidence_artifact_ids,
                checks_executed=checks_executed,
            )
        )

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
        producer: ProducerIdentity | None = None,
        project_id: str | None = None,
        capability_ids: tuple[str, ...] | None = None,
        _replace_context: bool = False,
        _canonical_subject_token: object | None = None,
    ) -> VerificationRequest:
        if (
            self._require_canonical_subjects
            and _canonical_subject_token is not _CANONICAL_SUBJECT_TOKEN
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "raw repaired verification subjects are disabled; use CanonicalVerificationRuntime",
            )
        previous = self.get_request(verification_id)
        previous_result = self.result_for(verification_id)
        if (
            previous_result is None
            or previous_result.outcome is not VerificationOutcome.NEEDS_CHANGES
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "reverification requires a completed needs_changes result",
            )
        policy = self.get_policy(previous.policy_id, previous.policy_version)
        next_attempt = previous.repair_attempt + 1
        if next_attempt > policy.max_repair_attempts:
            raise ContractError(ErrorCode.CONFLICT, "verification repair limit exhausted")
        if new_subject == previous.subject:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "repair must produce a new exact subject revision or digest",
            )
        return self.request_verification(
            task_id=previous.task_id,
            policy_id=previous.policy_id,
            policy_version=previous.policy_version,
            stage_id=previous.stage_id,
            subject=new_subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id if _replace_context else previous.project_id,
            capability_ids=(
                capability_ids
                if _replace_context and capability_ids is not None
                else previous.capability_ids
            ),
            producer=producer if _replace_context else previous.producer,
            repair_attempt=next_attempt,
            causation_id=causation_id,
            _canonical_subject_token=_canonical_subject_token,
        )

    def assess_completion(
        self,
        *,
        task_id: str,
        subject: VerificationSubject,
        policy_id: str | None = None,
        policy_version: int | None = None,
        now: datetime | None = None,
    ) -> CompletionAssessment:
        if policy_id is None:
            if policy_version is not None:
                raise ValueError("policy_version requires policy_id")
            return CompletionAssessment(
                task_id=task_id,
                subject=subject,
                state=CompletionState.ACCEPTED,
                reason="no verification policy is required",
            )
        if policy_version is None:
            raise ValueError("policy_id requires policy_version")
        policy = self.get_policy(policy_id, policy_version)
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("completion assessment time must be timezone-aware")
        if not policy.stages:
            return CompletionAssessment(
                task_id=task_id,
                subject=subject,
                state=CompletionState.ACCEPTED,
                reason="verification policy contains no required stages",
                policy_id=policy_id,
                policy_version=policy_version,
            )

        # Definitive outcomes must be evaluated across every stage before an
        # earlier incomplete stage can return WAITING. This makes the policy invariant
        # "any critical failure blocks completion" independent of stage ordering.
        stage_matches = {
            stage.stage_id: self._matching_stage_results(
                task_id=task_id,
                subject=subject,
                policy=policy,
                stage_id=stage.stage_id,
                now=current,
            )
            for stage in policy.stages
        }
        observed_repair_attempt = max(
            (
                request.repair_attempt
                for matching in stage_matches.values()
                for request, _result in matching
            ),
            default=0,
        )
        for stage in policy.stages:
            matching = stage_matches[stage.stage_id]
            if stage.critical and any(
                result.outcome is VerificationOutcome.FAIL for _request, result in matching
            ):
                return CompletionAssessment(
                    task_id=task_id,
                    subject=subject,
                    state=self._failure_state(policy),
                    reason="critical verification stage failed",
                    policy_id=policy_id,
                    policy_version=policy_version,
                    blocking_verification_ids=tuple(
                        request.verification_id
                        for request, result in matching
                        if result.outcome is VerificationOutcome.FAIL
                    ),
                    repair_attempts_remaining=max(
                        0, policy.max_repair_attempts - observed_repair_attempt
                    ),
                )
        for stage in policy.stages:
            matching = stage_matches[stage.stage_id]
            if any(
                result.outcome is VerificationOutcome.NEEDS_CHANGES for _request, result in matching
            ):
                remaining = max(0, policy.max_repair_attempts - observed_repair_attempt)
                state = (
                    CompletionState.REPAIR_REQUIRED
                    if remaining > 0
                    else self._failure_state(policy)
                )
                return CompletionAssessment(
                    task_id=task_id,
                    subject=subject,
                    state=state,
                    reason=(
                        "verification requested changes"
                        if remaining > 0
                        else "verification repair limit exhausted"
                    ),
                    policy_id=policy_id,
                    policy_version=policy_version,
                    blocking_verification_ids=tuple(
                        request.verification_id
                        for request, result in matching
                        if result.outcome is VerificationOutcome.NEEDS_CHANGES
                    ),
                    repair_attempts_remaining=remaining,
                )

        blocking: list[str] = []
        max_repair_attempt = 0
        for stage in policy.stages:
            matching = self._matching_stage_results(
                task_id=task_id,
                subject=subject,
                policy=policy,
                stage_id=stage.stage_id,
                now=current,
            )
            if matching:
                max_repair_attempt = max(
                    max_repair_attempt,
                    max(request.repair_attempt for request, _result in matching),
                )
            if any(
                result.outcome is VerificationOutcome.NEEDS_CHANGES for _request, result in matching
            ):
                remaining = max(0, policy.max_repair_attempts - max_repair_attempt)
                state = (
                    CompletionState.REPAIR_REQUIRED
                    if remaining > 0
                    else self._failure_state(policy)
                )
                return CompletionAssessment(
                    task_id=task_id,
                    subject=subject,
                    state=state,
                    reason=(
                        "verification requested changes"
                        if remaining > 0
                        else "verification repair limit exhausted"
                    ),
                    policy_id=policy_id,
                    policy_version=policy_version,
                    blocking_verification_ids=tuple(
                        request.verification_id
                        for request, result in matching
                        if result.outcome is VerificationOutcome.NEEDS_CHANGES
                    ),
                    repair_attempts_remaining=remaining,
                )
            if stage.critical and any(
                result.outcome is VerificationOutcome.FAIL for _request, result in matching
            ):
                return CompletionAssessment(
                    task_id=task_id,
                    subject=subject,
                    state=self._failure_state(policy),
                    reason="critical verification stage failed",
                    policy_id=policy_id,
                    policy_version=policy_version,
                    blocking_verification_ids=tuple(
                        request.verification_id
                        for request, result in matching
                        if result.outcome is VerificationOutcome.FAIL
                    ),
                )

            accepted = [
                (request, result)
                for request, result in matching
                if result.outcome in stage.accepted_outcomes
            ]
            if policy.independence.require_distinct_verifiers:
                distinct = {
                    self._distinct_verifier_key(result.verifier) for _request, result in accepted
                }
                accepted_count = len(distinct)
            else:
                accepted_count = len(accepted)
            if accepted_count < stage.minimum_results:
                stage_requests = self._matching_stage_requests(
                    task_id=task_id,
                    subject=subject,
                    policy=policy,
                    stage_id=stage.stage_id,
                    now=current,
                )
                expired = [
                    request
                    for request in stage_requests
                    if request.status is VerificationRequestStatus.EXPIRED
                ]
                if expired:
                    return CompletionAssessment(
                        task_id=task_id,
                        subject=subject,
                        state=self._failure_state_for(policy.timeout_failure_policy),
                        reason=f"verification stage {stage.stage_id!r} timed out",
                        policy_id=policy_id,
                        policy_version=policy_version,
                        blocking_verification_ids=tuple(
                            request.verification_id for request in expired
                        ),
                        repair_attempts_remaining=max(
                            0, policy.max_repair_attempts - max_repair_attempt
                        ),
                    )
                blocking.extend(request.verification_id for request in stage_requests)
                return CompletionAssessment(
                    task_id=task_id,
                    subject=subject,
                    state=CompletionState.WAITING,
                    reason=f"verification stage {stage.stage_id!r} is incomplete",
                    policy_id=policy_id,
                    policy_version=policy_version,
                    blocking_verification_ids=tuple(blocking),
                    repair_attempts_remaining=max(
                        0, policy.max_repair_attempts - max_repair_attempt
                    ),
                )

        return CompletionAssessment(
            task_id=task_id,
            subject=subject,
            state=CompletionState.ACCEPTED,
            reason="all required verification stages passed",
            policy_id=policy_id,
            policy_version=policy_version,
            repair_attempts_remaining=max(0, policy.max_repair_attempts - max_repair_attempt),
        )

    def audit_history(
        self,
        *,
        task_id: str | None = None,
        verification_id: str | None = None,
    ) -> tuple[VerificationAuditEvent, ...]:
        """Return immutable canonical audit history in append order."""

        return tuple(
            event
            for event in self._audit_events
            if (task_id is None or event.task_id == task_id)
            and (verification_id is None or event.verification_id == verification_id)
        )

    def history(
        self,
        *,
        task_id: str,
    ) -> tuple[tuple[VerificationRequest, VerificationResult | None], ...]:
        items = [
            (request, self.result_for(request.verification_id))
            for request in self._requests.values()
            if request.task_id == task_id
        ]
        items.sort(key=lambda item: (item[0].created_at, item[0].verification_id))
        return tuple(items)

    def _matching_stage_requests(
        self,
        *,
        task_id: str,
        subject: VerificationSubject,
        policy: VerificationPolicy,
        stage_id: str,
        now: datetime,
    ) -> list[VerificationRequest]:
        matches: list[VerificationRequest] = []
        for stored in list(self._requests.values()):
            if (
                stored.task_id != task_id
                or stored.policy_id != policy.policy_id
                or stored.policy_version != policy.version
                or stored.stage_id != stage_id
                or stored.subject != subject
            ):
                continue
            matches.append(self.get_request(stored.verification_id, now=now))
        return matches

    def _matching_stage_results(
        self,
        *,
        task_id: str,
        subject: VerificationSubject,
        policy: VerificationPolicy,
        stage_id: str,
        now: datetime,
    ) -> list[tuple[VerificationRequest, VerificationResult]]:
        matches: list[tuple[VerificationRequest, VerificationResult]] = []
        for request in self._matching_stage_requests(
            task_id=task_id,
            subject=subject,
            policy=policy,
            stage_id=stage_id,
            now=now,
        ):
            result = self.result_for(request.verification_id)
            if result is None:
                continue
            if policy.result_expiry_seconds is not None:
                expires_at = result.completed_at + timedelta(seconds=policy.result_expiry_seconds)
                if expires_at <= now:
                    continue
            matches.append((request, result))
        return matches

    @staticmethod
    def _distinct_verifier_key(verifier: VerifierIdentity) -> str:
        if verifier.kind is VerifierKind.AGENT:
            assert verifier.agent_id is not None
            return f"agent:{verifier.agent_id}"
        if verifier.kind is VerifierKind.HUMAN:
            return f"human:{verifier.verifier_ref}"
        if verifier.kind is VerifierKind.PROVIDER:
            provider = verifier.provider_id or "unknown-provider"
            return f"provider:{provider}:{verifier.verifier_ref}"
        return f"deterministic:{verifier.verifier_ref}"

    @staticmethod
    def _failure_state_for(
        failure_policy: VerificationFailurePolicy,
    ) -> CompletionState:
        if failure_policy is VerificationFailurePolicy.WAIT:
            return CompletionState.WAITING
        if failure_policy is VerificationFailurePolicy.ESCALATE:
            return CompletionState.ESCALATED
        return CompletionState.REJECTED

    @classmethod
    def _failure_state(cls, policy: VerificationPolicy) -> CompletionState:
        return cls._failure_state_for(policy.failure_policy)

    @staticmethod
    def _enforce_independence(
        policy: VerificationPolicy,
        request: VerificationRequest,
        verifier: VerifierIdentity,
    ) -> None:
        rules = policy.independence
        producer = request.producer

        if verifier.kind is VerifierKind.AGENT and rules.agent_reviewer_must_be_read_only:
            if not verifier.read_only:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy requires read-only reviewer-agent capabilities",
                )

        risk_forbids_self = (
            policy.risk_classification in rules.forbid_self_verification_risk_classes
        )
        requires_producer = (
            (rules.producer_agent_must_differ and verifier.kind is VerifierKind.AGENT)
            or rules.model_must_differ
            or rules.provider_must_differ
            or (rules.human_reviewer_must_differ and verifier.kind is VerifierKind.HUMAN)
            or rules.forbid_self_verification
            or risk_forbids_self
        )
        if requires_producer and producer is None:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "verification policy cannot prove required producer identity",
            )
        if producer is None:
            return

        if rules.producer_agent_must_differ and verifier.kind is VerifierKind.AGENT:
            if producer.agent_id is None or verifier.agent_id is None:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy cannot prove producer/reviewer agent independence",
                )
            if verifier.agent_id == producer.agent_id:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy requires reviewer agent to differ from producer",
                )

        if rules.model_must_differ:
            if producer.model_config_id is None or verifier.model_config_id is None:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy cannot prove reviewer model independence",
                )
            if verifier.model_config_id == producer.model_config_id:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy requires reviewer model to differ from producer model",
                )

        if rules.provider_must_differ:
            if producer.provider_id is None or verifier.provider_id is None:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy cannot prove reviewer provider independence",
                )
            if verifier.provider_id == producer.provider_id:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy requires reviewer provider to differ "
                    "from producer provider",
                )

        if rules.human_reviewer_must_differ and verifier.kind is VerifierKind.HUMAN:
            if verifier.verifier_ref == producer.actor_ref:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy requires a separate human reviewer",
                )

        if rules.forbid_self_verification or risk_forbids_self:
            same_actor = verifier.verifier_ref == producer.actor_ref
            same_agent = (
                verifier.kind is VerifierKind.AGENT
                and producer.agent_id is not None
                and verifier.agent_id == producer.agent_id
            )
            if same_actor or same_agent:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy forbids self-verification",
                )

    @staticmethod
    def _require_policy_scope(
        policy: VerificationPolicy,
        *,
        task_id: str,
        project_id: str | None,
        capability_ids: tuple[str, ...],
        producer: ProducerIdentity | None,
    ) -> None:
        if policy.scope.task_ids and task_id not in policy.scope.task_ids:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "verification policy does not apply to this task",
            )
        if policy.scope.project_ids and project_id not in policy.scope.project_ids:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "verification policy does not apply to this project",
            )
        if policy.scope.capability_ids and not set(capability_ids).intersection(
            policy.scope.capability_ids
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "verification policy does not apply to these capabilities",
            )
        if policy.scope.agent_ids:
            producer_agent_id = None if producer is None else producer.agent_id
            if producer_agent_id not in policy.scope.agent_ids:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "verification policy does not apply to this producer agent",
                )
