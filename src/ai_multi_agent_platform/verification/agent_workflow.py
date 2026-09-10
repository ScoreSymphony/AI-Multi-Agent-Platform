"""Automatic reviewer-Agent workflow coordination for issue #711.

This module productively wires canonical Verification to the normal Agent runtime.
Provider/orchestrator-specific reviewer execution remains replaceable, and repair
always starts through the existing canonical VerificationRepairRuntime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

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
        """Drive one Agent-verifier request through review and bounded repair."""

        cycles: list[ReviewWorkflowCycle] = []
        current_id = verification_id
        runtime_options = options or ReviewerRuntimeOptions()

        while True:
            request = self._completion.verification.get_request(current_id)
            self._require_agent_request(request)
            existing = self._review_run_for(request.verification_id)

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
                        reviewer_run=existing,
                        verification_result=result,
                    )
                )
            elif request.status is VerificationRequestStatus.PENDING:
                if existing is not None:
                    if existing.status is AgentRunStatus.RUNNING:
                        cycles.append(
                            ReviewWorkflowCycle(
                                request=request,
                                reviewer_run=existing,
                                verification_result=None,
                            )
                        )
                        return ReviewWorkflowResult(
                            cycles=tuple(cycles),
                            completion=self._completion.assess_task_completion(
                                request.task_id
                            ),
                        )
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "pending verification already has a terminal reviewer AgentRun "
                        "and requires reconciliation",
                    )

                reviewer_run = await self._start_reviewer(request, runtime_options)
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
        return ReviewWorkflowResult(
            cycles=(
                ReviewWorkflowCycle(
                    request=request,
                    reviewer_run=self._review_run_for(verification_id),
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
                telemetry=execution.telemetry,
            )
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

    @staticmethod
    def _require_agent_request(request: VerificationRequest) -> None:
        if request.requested_verifier_kind is not VerifierKind.AGENT:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "automatic reviewer workflow requires an Agent-verifier request",
            )

    def _review_run_for(self, verification_id: str) -> AgentRunRecord | None:
        matches = [
            record
            for record in self._agents.service.repository.list_agent_runs()
            if record.verification_context.get("schema") == _REVIEW_CONTEXT_SCHEMA
            and record.verification_context.get("verification_id") == verification_id
        ]
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification maps to multiple reviewer AgentRuns",
            )
        return None if not matches else matches[0]


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
