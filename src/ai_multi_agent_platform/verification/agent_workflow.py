"""Automatic reviewer-Agent workflow coordination for issue #711.

This module productively wires canonical Verification to the normal Agent runtime.
Provider/orchestrator-specific reviewer execution remains replaceable, and repair
always starts through the existing canonical VerificationRepairRuntime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from threading import Lock as ThreadLock
from typing import Protocol, runtime_checkable
from weakref import WeakKeyDictionary

from ai_multi_agent_platform.agents import (
    AgentOrchestratorMapper,
    AgentRunRecord,
    AgentRunStatus,
    AgentRuntime,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.models import RoutingRequirements

from .evidence import CanonicalVerificationRuntime
from .gate import CompletionGateDecision, VerificationCompletionAuthority
from .models import (
    CompletionState,
    VerificationFinding,
    VerificationOutcome,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerifierKind,
)
from .repair import VerificationRepairExecution, VerificationRepairRuntime
from .reviewer_agent import ReviewerAgentRuntime

_REVIEW_CONTEXT_SCHEMA = "verification-reviewer-agent-v1"
_STAGED_DECISION_KEY = "automatic_reviewer_decision"
_STAGED_DECISION_SCHEMA = "automatic-reviewer-decision-v1"
_REVIEW_LOCKS: WeakKeyDictionary[object, dict[str, asyncio.Lock]] = WeakKeyDictionary()
_REVIEW_LOCKS_GUARD = ThreadLock()


def _review_lock(repository: object, verification_id: str) -> asyncio.Lock:
    """Share one in-process dispatch lock across workflow instances for a repository."""

    with _REVIEW_LOCKS_GUARD:
        locks = _REVIEW_LOCKS.get(repository)
        if locks is None:
            locks = {}
            _REVIEW_LOCKS[repository] = locks
        lock = locks.get(verification_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[verification_id] = lock
        return lock


@dataclass(frozen=True, slots=True)
class ReviewerAssignment:
    """Configured reviewer route pinned to an Agent or exact Team member."""

    agent_id: str | None = None
    agent_revision: int | None = None
    team_id: str | None = None
    team_revision: int | None = None
    team_role: str | None = None

    def __post_init__(self) -> None:
        if self.team_id is None:
            if self.agent_id is None or self.agent_revision is None:
                raise ValueError(
                    "standalone reviewer assignment requires exact agent revision"
                )
            if self.team_revision is not None or self.team_role is not None:
                raise ValueError("team reviewer fields require team_id")
        else:
            validate_id(self.team_id, "team")
            if self.team_revision is None or self.team_revision < 1:
                raise ValueError("team reviewer assignment requires team_revision >= 1")
            if self.agent_id is None and self.team_role is None:
                raise ValueError("team reviewer assignment requires agent_id or team_role")
            if self.agent_id is not None and self.team_role is not None:
                raise ValueError("team reviewer assignment must choose agent_id or team_role")

        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")
            if self.agent_revision is None or self.agent_revision < 1:
                raise ValueError("reviewer assignment requires agent_revision >= 1")
        elif self.agent_revision is not None:
            raise ValueError("agent_revision requires agent_id")

        if self.team_role is not None and not self.team_role.strip():
            raise ValueError("team reviewer role must not be blank")


@dataclass(frozen=True, slots=True)
class ResolvedReviewerAssignment:
    """Exact reviewer identity used for one dispatch."""

    agent_id: str
    agent_revision: int
    team_id: str | None = None
    team_revision: int | None = None

    def __post_init__(self) -> None:
        validate_id(self.agent_id, "agent")
        if self.agent_revision < 1:
            raise ValueError("resolved reviewer agent_revision must be >= 1")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
            if self.team_revision is None or self.team_revision < 1:
                raise ValueError("resolved reviewer team requires revision >= 1")
        elif self.team_revision is not None:
            raise ValueError("resolved reviewer team_revision requires team_id")


@runtime_checkable
class ReviewerAssignmentResolver(Protocol):
    """Resolve a pending Agent-verifier request to an exact reviewer revision."""

    def resolve(
        self,
        request: VerificationRequest,
        agents: AgentRuntime,
    ) -> ResolvedReviewerAssignment: ...


class ConfiguredReviewerResolver:
    """Deterministic policy/stage reviewer routing with no implicit fallback."""

    def __init__(
        self,
        assignments: Mapping[tuple[str, int, str], ReviewerAssignment],
    ) -> None:
        self._assignments = dict(assignments)

    def resolve(
        self,
        request: VerificationRequest,
        agents: AgentRuntime,
    ) -> ResolvedReviewerAssignment:
        key = (request.policy_id, request.policy_version, request.stage_id)
        try:
            assignment = self._assignments[key]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "no reviewer Agent is configured for verification policy stage",
                details={
                    "policy_id": request.policy_id,
                    "policy_version": request.policy_version,
                    "stage_id": request.stage_id,
                },
            ) from exc

        if assignment.team_id is None:
            assert assignment.agent_id is not None
            assert assignment.agent_revision is not None
            agents.service.get_agent_revision(
                assignment.agent_id,
                assignment.agent_revision,
            )
            return ResolvedReviewerAssignment(
                agent_id=assignment.agent_id,
                agent_revision=assignment.agent_revision,
            )

        assert assignment.team_revision is not None
        team = agents.service.get_team_revision(
            assignment.team_id,
            assignment.team_revision,
        )
        if not team.profile.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"reviewer Agent Team is disabled: {team.team_id}@{team.revision}",
            )

        if assignment.agent_id is not None:
            assert assignment.agent_revision is not None
            matches = [
                member
                for member in team.profile.members
                if member.agent.agent_id == assignment.agent_id
                and member.agent.revision == assignment.agent_revision
            ]
        else:
            assert assignment.team_role is not None
            requested_role = assignment.team_role.strip().casefold()
            matches = [
                member
                for member in team.profile.members
                if member.role.strip().casefold() == requested_role
            ]

        if len(matches) != 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "reviewer Agent Team selection must resolve exactly one member",
                details={
                    "team_id": team.team_id,
                    "team_revision": team.revision,
                    "match_count": len(matches),
                },
            )

        member = matches[0]
        return ResolvedReviewerAssignment(
            agent_id=member.agent.agent_id,
            agent_revision=member.agent.revision,
            team_id=team.team_id,
            team_revision=team.revision,
        )


@dataclass(frozen=True, slots=True)
class ReviewerRuntimeOptions:
    """Trusted runtime inputs required to start a reviewer Agent."""

    mapper: AgentOrchestratorMapper | None = None
    task_model_override: RoutingRequirements | None = None
    requested_capability_ids: tuple[str, ...] = ()
    available_capability_ids: frozenset[str] = frozenset()
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    task_context: Mapping[str, JsonValue] = field(default_factory=dict)
    project_context: Mapping[str, JsonValue] = field(default_factory=dict)
    max_reviewer_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_reviewer_attempts < 1:
            raise ValueError("max_reviewer_attempts must be >= 1")


@dataclass(frozen=True, slots=True)
class ReviewerExecutionDecision:
    """Structured output from a replaceable reviewer execution adapter."""

    outcome: VerificationOutcome
    findings: tuple[VerificationFinding, ...] = ()
    evidence_artifact_ids: tuple[str, ...] = ()
    checks_executed: tuple[str, ...] = ("agent_review",)
    output_artifact_ids: tuple[str, ...] = ()
    output_result_ids: tuple[str, ...] = ()
    model_call_refs: tuple[str, ...] = ()
    tool_invocation_refs: tuple[str, ...] = ()
    telemetry: Mapping[str, JsonValue] = field(default_factory=dict)


@runtime_checkable
class ReviewerAgentExecutor(Protocol):
    """Provider/orchestrator seam executing an already pinned reviewer AgentRun."""

    async def execute_review(
        self,
        *,
        request: VerificationRequest,
        agent_run: AgentRunRecord,
    ) -> ReviewerExecutionDecision: ...


@dataclass(frozen=True, slots=True)
class RepairOutput:
    """New canonical Result/Artifact produced by an already-started repair Run."""

    subject_type: str
    subject_id: str
    correlation_id: str
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if self.subject_type not in {"result", "artifact"}:
            raise ValueError("repair output subject_type must be result or artifact")
        validate_id(self.subject_id, self.subject_type)
        if not self.correlation_id.strip():
            raise ValueError("repair output correlation_id must not be blank")
        if self.causation_id is not None and not self.causation_id.strip():
            raise ValueError("repair output causation_id must not be blank")


@runtime_checkable
class ReviewerRepairExecutor(Protocol):
    """Complete a repair Run already created by VerificationRepairRuntime.

    Implementations may coordinate the producer Agent/Team or wait for an external
    Worker, but they must not create a second repair lifecycle. They return only the
    newly produced canonical Result/Artifact reference for exact re-verification.
    """

    async def execute_repair(
        self,
        *,
        execution: VerificationRepairExecution,
        request: VerificationRequest,
        review_result: VerificationResult,
    ) -> RepairOutput: ...


@dataclass(frozen=True, slots=True)
class ReviewWorkflowCycle:
    request: VerificationRequest
    reviewer_run: AgentRunRecord | None
    verification_result: VerificationResult | None
    repair_execution: VerificationRepairExecution | None = None


@dataclass(frozen=True, slots=True)
class ReviewWorkflowResult:
    """Observable result of automatic review and optional bounded repair."""

    cycles: tuple[ReviewWorkflowCycle, ...]
    completion: CompletionGateDecision

    @property
    def latest(self) -> ReviewWorkflowCycle:
        if not self.cycles:
            raise RuntimeError("review workflow result has no cycles")
        return self.cycles[-1]


class AutomaticReviewerWorkflow:
    """Coordinate canonical Agent review without becoming lifecycle authority."""

    def __init__(
        self,
        *,
        runtime: CanonicalVerificationRuntime,
        completion: VerificationCompletionAuthority,
        agents: AgentRuntime,
        resolver: ReviewerAssignmentResolver,
        executor: ReviewerAgentExecutor,
        repair_runtime: VerificationRepairRuntime | None = None,
        repair_executor: ReviewerRepairExecutor | None = None,
    ) -> None:
        if (repair_runtime is None) != (repair_executor is None):
            raise ValueError(
                "automatic repair requires both VerificationRepairRuntime and "
                "ReviewerRepairExecutor"
            )
        self._runtime = runtime
        self._completion = completion
        self._agents = agents
        self._resolver = resolver
        self._executor = executor
        self._repair_runtime = repair_runtime
        self._repair_executor = repair_executor
        self._reviewer = ReviewerAgentRuntime(
            completion.verification,
            agents,
            evidence=runtime.evidence,
            canonical_runtime=runtime,
        )

    async def request_and_run(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        subject_type: str,
        subject_id: str,
        correlation_id: str,
        causation_id: str | None = None,
        options: ReviewerRuntimeOptions | None = None,
    ) -> ReviewWorkflowResult:
        """Create exact Verification and immediately drive configured Agent review."""

        request = await self._runtime.request_verification(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return await self.run_request(request.verification_id, options=options)

    async def run_request(
        self,
        verification_id: str,
        *,
        options: ReviewerRuntimeOptions | None = None,
    ) -> ReviewWorkflowResult:
        """Drive one Agent-verifier request through review and bounded repair.

        Dispatch is serialized per canonical Agent repository + Verification ID across
        workflow instances in the current Control Plane process. The durable AgentRun
        binding remains the recovery source of truth after the lock is released/restarted.
        """

        validate_id(verification_id, "verification")
        lock = _review_lock(self._agents.service.repository, verification_id)
        async with lock:
            return await self._run_request_locked(
                verification_id,
                options=options or ReviewerRuntimeOptions(),
            )

    async def _run_request_locked(
        self,
        verification_id: str,
        *,
        options: ReviewerRuntimeOptions,
    ) -> ReviewWorkflowResult:
        cycles: list[ReviewWorkflowCycle] = []
        current_id = verification_id

        while True:
            request = self._completion.verification.get_request(current_id)
            self._require_agent_request(request)
            runs = self._review_runs_for(request.verification_id)
            latest = None if not runs else runs[-1]

            if request.status is VerificationRequestStatus.COMPLETED:
                result = self._completion.verification.result_for(
                    request.verification_id
                )
                if result is None:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "completed verification request has no canonical result",
                    )
                cycles.append(
                    ReviewWorkflowCycle(
                        request=request,
                        reviewer_run=latest,
                        verification_result=result,
                    )
                )
            elif request.status is VerificationRequestStatus.PENDING:
                active = [run for run in runs if run.status is AgentRunStatus.RUNNING]
                if len(active) > 1:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "verification maps to multiple running reviewer AgentRuns",
                    )

                if active:
                    reviewer_run = active[0]
                    staged = self._staged_decision(reviewer_run)
                    if staged is None:
                        cycles.append(
                            ReviewWorkflowCycle(
                                request=request,
                                reviewer_run=reviewer_run,
                                verification_result=None,
                            )
                        )
                        return ReviewWorkflowResult(
                            cycles=tuple(cycles),
                            completion=self._completion.assess_task_completion(
                                request.task_id
                            ),
                        )
                    result = await self._resume_submission(
                        reviewer_run,
                        staged,
                    )
                elif latest is not None and latest.status is AgentRunStatus.SUCCEEDED:
                    staged = self._staged_decision(latest)
                    if staged is None:
                        raise ContractError(
                            ErrorCode.CONFLICT,
                            "successful reviewer AgentRun has no recoverable staged decision",
                        )
                    reviewer_run = latest
                    result = await self._resume_submission(reviewer_run, staged)
                else:
                    failed_attempts = sum(
                        run.status in {AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
                        for run in runs
                    )
                    if failed_attempts >= options.max_reviewer_attempts:
                        raise ContractError(
                            ErrorCode.UNAVAILABLE,
                            "automatic reviewer retry budget is exhausted",
                            details={
                                "verification_id": request.verification_id,
                                "attempts": failed_attempts,
                                "max_reviewer_attempts": options.max_reviewer_attempts,
                            },
                        )
                    reviewer_run = await self._start_reviewer(request, options)
                    result = await self._execute_reviewer(request, reviewer_run)

                cycles.append(
                    ReviewWorkflowCycle(
                        request=self._completion.verification.get_request(
                            request.verification_id
                        ),
                        reviewer_run=self._agents.service.repository.get_agent_run(
                            reviewer_run.agent_run_id
                        ),
                        verification_result=result,
                    )
                )
            else:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"verification request cannot run from {request.status.value}",
                )

            completion = self._completion.assess_task_completion(request.task_id)
            if completion.state is not CompletionState.REPAIR_REQUIRED:
                return ReviewWorkflowResult(
                    cycles=tuple(cycles),
                    completion=completion,
                )
            if self._repair_runtime is None or self._repair_executor is None:
                return ReviewWorkflowResult(
                    cycles=tuple(cycles),
                    completion=completion,
                )

            current_cycle = cycles[-1]
            result = current_cycle.verification_result
            if result is None or result.outcome is not VerificationOutcome.NEEDS_CHANGES:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repair-required completion lacks a needs_changes verification result",
                )

            repair_execution = await self._repair_runtime.start_repair(
                current_cycle.request.verification_id,
                idempotency_key=(
                    f"automatic-review:{current_cycle.request.verification_id}:repair"
                ),
                actor_ref="service:automatic-reviewer-workflow",
            )
            cycles[-1] = replace(
                current_cycle,
                repair_execution=repair_execution,
            )
            repaired = await self._repair_executor.execute_repair(
                execution=repair_execution,
                request=current_cycle.request,
                review_result=result,
            )
            next_request = await self._runtime.request_reverification_after_repair(
                current_cycle.request.verification_id,
                subject_type=repaired.subject_type,
                subject_id=repaired.subject_id,
                correlation_id=repaired.correlation_id,
                causation_id=repaired.causation_id,
            )
            current_id = next_request.verification_id

    def status_for(self, verification_id: str) -> ReviewWorkflowResult:
        """Read canonical review state without executing or retrying work."""

        request = self._completion.verification.get_request(verification_id)
        result = self._completion.verification.result_for(verification_id)
        runs = self._review_runs_for(verification_id)
        return ReviewWorkflowResult(
            cycles=(
                ReviewWorkflowCycle(
                    request=request,
                    reviewer_run=None if not runs else runs[-1],
                    verification_result=result,
                ),
            ),
            completion=self._completion.assess_task_completion(request.task_id),
        )

    async def _start_reviewer(
        self,
        request: VerificationRequest,
        options: ReviewerRuntimeOptions,
    ) -> AgentRunRecord:
        selected = self._resolver.resolve(request, self._agents)
        if request.run_id is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer dispatch requires a canonical Run binding",
            )
        return await self._reviewer.start_review(
            request.verification_id,
            run_id=request.run_id,
            agent_id=selected.agent_id,
            revision=selected.agent_revision,
            team_id=selected.team_id,
            team_revision=selected.team_revision,
            mapper=options.mapper,
            task_model_override=options.task_model_override,
            requested_capability_ids=options.requested_capability_ids,
            available_capability_ids=options.available_capability_ids,
            granted_permissions=options.granted_permissions,
            available_worker_capabilities=options.available_worker_capabilities,
            task_context=options.task_context,
            project_context=options.project_context,
        )

    async def _execute_reviewer(
        self,
        request: VerificationRequest,
        reviewer_run: AgentRunRecord,
    ) -> VerificationResult:
        try:
            execution = await self._executor.execute_review(
                request=request,
                agent_run=reviewer_run,
            )
        except asyncio.CancelledError:
            current = self._agents.service.repository.get_agent_run(
                reviewer_run.agent_run_id
            )
            if current.status is AgentRunStatus.RUNNING:
                self._agents.finish_agent_run(
                    current.agent_run_id,
                    status=AgentRunStatus.CANCELLED,
                    error="automatic reviewer execution cancelled",
                )
            raise
        except Exception as exc:
            current = self._agents.service.repository.get_agent_run(
                reviewer_run.agent_run_id
            )
            if current.status is AgentRunStatus.RUNNING:
                self._agents.finish_agent_run(
                    current.agent_run_id,
                    status=AgentRunStatus.FAILED,
                    error=str(exc),
                )
            raise

        staged = self._stage_execution_decision(reviewer_run, execution)
        try:
            return await self._reviewer.complete_review(
                reviewer_run.agent_run_id,
                outcome=execution.outcome,
                findings=execution.findings,
                evidence_artifact_ids=execution.evidence_artifact_ids,
                checks_executed=execution.checks_executed,
                output_artifact_ids=execution.output_artifact_ids,
                output_result_ids=execution.output_result_ids,
                model_call_refs=execution.model_call_refs,
                tool_invocation_refs=execution.tool_invocation_refs,
                telemetry=staged.telemetry,
            )
        except asyncio.CancelledError:
            # If execution already produced and staged a decision, keep the run recoverable.
            # complete_review may already have terminalized it before canonical submission.
            raise
        except Exception as exc:
            current = self._agents.service.repository.get_agent_run(
                reviewer_run.agent_run_id
            )
            if current.status is AgentRunStatus.RUNNING:
                self._agents.finish_agent_run(
                    current.agent_run_id,
                    status=AgentRunStatus.FAILED,
                    error=str(exc),
                    telemetry=current.telemetry,
                )
            raise

    def _stage_execution_decision(
        self,
        reviewer_run: AgentRunRecord,
        decision: ReviewerExecutionDecision,
    ) -> AgentRunRecord:
        """Persist reviewer output before the non-atomic AgentRun/result handoff."""

        current = self._agents.service.repository.get_agent_run(reviewer_run.agent_run_id)
        if current.status is not AgentRunStatus.RUNNING:
            raise ContractError(
                ErrorCode.CONFLICT,
                "reviewer execution decision can only be staged on a running AgentRun",
            )
        telemetry = dict(current.telemetry)
        telemetry.update(decision.telemetry)
        telemetry[_STAGED_DECISION_KEY] = self._encode_decision(decision)
        staged = replace(current, telemetry=telemetry)
        self._agents.service.repository.update_agent_run(staged)
        return staged

    async def _resume_submission(
        self,
        reviewer_run: AgentRunRecord,
        decision: ReviewerExecutionDecision,
    ) -> VerificationResult:
        """Resubmit a durable staged decision without re-running the reviewer model."""

        if reviewer_run.status is AgentRunStatus.RUNNING:
            return await self._reviewer.complete_review(
                reviewer_run.agent_run_id,
                outcome=decision.outcome,
                findings=decision.findings,
                evidence_artifact_ids=decision.evidence_artifact_ids,
                checks_executed=decision.checks_executed,
                output_artifact_ids=decision.output_artifact_ids,
                output_result_ids=decision.output_result_ids,
                model_call_refs=decision.model_call_refs,
                tool_invocation_refs=decision.tool_invocation_refs,
                telemetry=reviewer_run.telemetry,
            )
        if reviewer_run.status is AgentRunStatus.SUCCEEDED:
            return await self._reviewer.complete_review(
                reviewer_run.agent_run_id,
                outcome=decision.outcome,
                findings=decision.findings,
                evidence_artifact_ids=decision.evidence_artifact_ids,
                checks_executed=decision.checks_executed,
            )
        raise ContractError(
            ErrorCode.CONFLICT,
            "only running/succeeded reviewer AgentRuns can reconcile staged decisions",
        )

    @staticmethod
    def _require_agent_request(request: VerificationRequest) -> None:
        if request.requested_verifier_kind is not VerifierKind.AGENT:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "automatic reviewer workflow requires an Agent-verifier request",
            )

    def _review_runs_for(self, verification_id: str) -> tuple[AgentRunRecord, ...]:
        return tuple(
            record
            for record in self._agents.service.repository.list_agent_runs()
            if record.verification_context.get("schema") == _REVIEW_CONTEXT_SCHEMA
            and record.verification_context.get("verification_id") == verification_id
        )

    @staticmethod
    def _encode_decision(decision: ReviewerExecutionDecision) -> dict[str, JsonValue]:
        return {
            "schema": _STAGED_DECISION_SCHEMA,
            "outcome": decision.outcome.value,
            "findings": [
                {
                    "code": finding.code,
                    "message": finding.message,
                    "severity": finding.severity,
                    "location_ref": finding.location_ref,
                }
                for finding in decision.findings
            ],
            "evidence_artifact_ids": list(decision.evidence_artifact_ids),
            "checks_executed": list(decision.checks_executed),
            "output_artifact_ids": list(decision.output_artifact_ids),
            "output_result_ids": list(decision.output_result_ids),
            "model_call_refs": list(decision.model_call_refs),
            "tool_invocation_refs": list(decision.tool_invocation_refs),
        }

    @staticmethod
    def _staged_decision(record: AgentRunRecord) -> ReviewerExecutionDecision | None:
        raw = record.telemetry.get(_STAGED_DECISION_KEY)
        if raw is None:
            return None
        if not isinstance(raw, dict) or raw.get("schema") != _STAGED_DECISION_SCHEMA:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewer AgentRun contains malformed staged decision metadata",
            )
        outcome_raw = raw.get("outcome")
        if not isinstance(outcome_raw, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "staged reviewer outcome is malformed",
            )
        try:
            outcome = VerificationOutcome(outcome_raw)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "staged reviewer outcome is unknown",
            ) from exc

        findings_raw = raw.get("findings", [])
        if not isinstance(findings_raw, list):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "staged reviewer findings are malformed",
            )
        findings: list[VerificationFinding] = []
        for item in findings_raw:
            if not isinstance(item, dict):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "staged reviewer finding is malformed",
                )
            code = item.get("code")
            message = item.get("message")
            severity = item.get("severity", "info")
            location_ref = item.get("location_ref")
            if (
                not isinstance(code, str)
                or not isinstance(message, str)
                or not isinstance(severity, str)
                or (location_ref is not None and not isinstance(location_ref, str))
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "staged reviewer finding fields are malformed",
                )
            findings.append(
                VerificationFinding(
                    code=code,
                    message=message,
                    severity=severity,
                    location_ref=location_ref,
                )
            )

        return ReviewerExecutionDecision(
            outcome=outcome,
            findings=tuple(findings),
            evidence_artifact_ids=_staged_string_tuple(raw, "evidence_artifact_ids"),
            checks_executed=_staged_string_tuple(raw, "checks_executed"),
            output_artifact_ids=_staged_string_tuple(raw, "output_artifact_ids"),
            output_result_ids=_staged_string_tuple(raw, "output_result_ids"),
            model_call_refs=_staged_string_tuple(raw, "model_call_refs"),
            tool_invocation_refs=_staged_string_tuple(raw, "tool_invocation_refs"),
        )


def _staged_string_tuple(raw: Mapping[str, JsonValue], field: str) -> tuple[str, ...]:
    value = raw.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"staged reviewer {field} is malformed",
        )
    return tuple(item for item in value if isinstance(item, str))


__all__ = [
    "AutomaticReviewerWorkflow",
    "ConfiguredReviewerResolver",
    "RepairOutput",
    "ResolvedReviewerAssignment",
    "ReviewWorkflowCycle",
    "ReviewWorkflowResult",
    "ReviewerAgentExecutor",
    "ReviewerAssignment",
    "ReviewerAssignmentResolver",
    "ReviewerExecutionDecision",
    "ReviewerRepairExecutor",
    "ReviewerRuntimeOptions",
]
