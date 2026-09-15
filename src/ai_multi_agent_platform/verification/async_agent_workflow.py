"""Async-safe automatic reviewer workflow for persistence-bearing Verification runtime paths."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents import AgentRunRecord, AgentRunStatus, AgentRuntime
from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ResolvedReviewerAssignment,
    ReviewerAgentExecutor,
    ReviewerAssignmentResolver,
    ReviewerRepairExecutor,
    ReviewerRuntimeOptions,
    ReviewWorkflowCycle,
    ReviewWorkflowResult,
)
from .async_persistence import (
    AsyncVerificationCompletionAuthority,
    AsyncVerificationService,
    runtime_verification_completion,
    runtime_verification_service,
)
from .evidence import CanonicalVerificationRuntime
from .gate import VerificationCompletionAuthority
from .models import (
    CompletionState,
    VerificationOutcome,
    VerificationRequest,
    VerificationRequestStatus,
)
from .repair import VerificationRepairRuntime
from .reviewer_agent import ReviewerAgentRuntime
from .reviewer_routing import CapabilityRoleReviewerResolver, ReviewerDiscoverySelector


class AsyncAutomaticReviewerWorkflow(AutomaticReviewerWorkflow):
    """Production reviewer workflow with awaitable Verification persistence access."""

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
        runtime_verification: AsyncVerificationService | None = None,
        runtime_completion: AsyncVerificationCompletionAuthority | None = None,
    ) -> None:
        super().__init__(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
            repair_runtime=repair_runtime,
            repair_executor=repair_executor,
        )
        self._runtime_verification = runtime_verification_service(
            completion.verification,
            runtime_service=runtime_verification,
        )
        self._runtime_completion = runtime_verification_completion(
            completion,
            runtime_completion=runtime_completion,
        )
        self._reviewer = ReviewerAgentRuntime(
            completion.verification,
            agents,
            evidence=runtime.evidence,
            canonical_runtime=runtime,
            runtime_verification=self._runtime_verification,
        )

    async def _resolve_reviewer_assignment(
        self,
        request: VerificationRequest,
    ) -> ResolvedReviewerAssignment:
        """Resolve policy-backed reviewer routing without inline Verification reads."""

        # PolicyMetadataReviewerResolver intentionally keeps its synchronous API for
        # setup/offline compatibility. Production async workflow resolves the same versioned
        # policy through the shared Verification persistence boundary before applying the
        # unchanged deterministic routing rules.
        from .output_workflow import (  # noqa: PLC0415 - avoids an import cycle at module load
            PolicyMetadataReviewerResolver,
            _automatic_review_configuration,
        )

        if not isinstance(self._resolver, PolicyMetadataReviewerResolver):
            return self._resolver.resolve(request, self._agents)

        policy = await self._runtime_verification.get_policy(
            request.policy_id,
            request.policy_version,
        )
        configuration = _automatic_review_configuration(policy, required=True)
        assert configuration is not None
        try:
            route = configuration.routes[request.stage_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer policy is missing a reviewer route for stage",
                details={
                    "policy_id": policy.policy_id,
                    "policy_version": policy.version,
                    "stage_id": request.stage_id,
                },
            ) from exc

        key = (request.policy_id, request.policy_version, request.stage_id)
        if isinstance(route, ReviewerDiscoverySelector):
            return CapabilityRoleReviewerResolver({key: route}).resolve(request, self._agents)
        return ConfiguredReviewerResolver({key: route}).resolve(request, self._agents)

    async def _start_reviewer(
        self,
        request: VerificationRequest,
        options: ReviewerRuntimeOptions,
    ) -> AgentRunRecord:
        selected = await self._resolve_reviewer_assignment(request)
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

    async def _run_request_locked(
        self,
        verification_id: str,
        *,
        options: ReviewerRuntimeOptions,
    ) -> ReviewWorkflowResult:
        cycles: list[ReviewWorkflowCycle] = []
        current_id = verification_id

        while True:
            request = await self._runtime_verification.get_request(current_id)
            self._require_agent_request(request)
            runs = self._review_runs_for(request.verification_id)
            latest = None if not runs else runs[-1]

            if request.status is VerificationRequestStatus.COMPLETED:
                result = await self._runtime_verification.result_for(request.verification_id)
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
                            completion=await self._runtime_completion.assess_task_completion(
                                request.task_id
                            ),
                        )
                    result = await self._resume_submission(reviewer_run, staged)
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
                        request=await self._runtime_verification.get_request(
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

            completion = await self._runtime_completion.assess_task_completion(request.task_id)
            if completion.state is not CompletionState.REPAIR_REQUIRED:
                return ReviewWorkflowResult(cycles=tuple(cycles), completion=completion)
            if self._repair_runtime is None or self._repair_executor is None:
                return ReviewWorkflowResult(cycles=tuple(cycles), completion=completion)

            current_cycle = cycles[-1]
            result = current_cycle.verification_result
            if result is None or result.outcome is not VerificationOutcome.NEEDS_CHANGES:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repair-required completion lacks a needs_changes verification result",
                )
            if current_cycle.reviewer_run is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "automatic repair requires the canonical reviewer AgentRun binding",
                )

            repair_execution = await self._repair_runtime.start_repair(
                current_cycle.request.verification_id,
                idempotency_key=(
                    f"automatic-review:{current_cycle.request.verification_id}:repair"
                ),
                actor_ref="service:automatic-reviewer-workflow",
            )
            cycles[-1] = replace(current_cycle, repair_execution=repair_execution)

            reviewer_run = self._agents.service.repository.get_agent_run(
                current_cycle.reviewer_run.agent_run_id
            )
            repaired = self._staged_repair_output(reviewer_run, repair_execution)
            if repaired is None:
                repaired = await self._repair_executor.execute_repair(
                    execution=repair_execution,
                    request=current_cycle.request,
                    review_result=result,
                )
                self._stage_repair_output(reviewer_run, repair_execution, repaired)

            next_request = await self._runtime.request_reverification_after_repair(
                current_cycle.request.verification_id,
                subject_type=repaired.subject_type,
                subject_id=repaired.subject_id,
                correlation_id=repaired.correlation_id,
                causation_id=repaired.causation_id,
            )
            current_id = next_request.verification_id


__all__ = ["AsyncAutomaticReviewerWorkflow"]
