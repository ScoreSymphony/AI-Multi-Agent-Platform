"""Execution and recovery orchestration for canonical compensation records."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CompensationIdempotency,
    InvocationStatus,
    InvocationTrace,
    ReversibilityClassification,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationControl, RetryMode

from .models import (
    CompensationActionProjection,
    CompensationAutomation,
    CompensationExecutionContext,
    CompensationFailureMode,
    CompensationGroup,
    CompensationGroupProjection,
    CompensationReconciliation,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    new_compensation_id,
    utc_now,
)
from .repository import CompensationRepository

ExecutionContextFactory = Callable[
    [CompensationRequest, CompletedSideEffect],
    Awaitable[CompensationExecutionContext],
]
ApprovalReferenceLookup = Callable[[str], str | None]


class CompensationReconciler(Protocol):
    """Check an ambiguous external compensation without repeating the side effect."""

    async def reconcile(
        self,
        request: CompensationRequest,
        action: CompletedSideEffect,
        result: CompensationResult,
    ) -> CompensationReconciliation: ...


class CompensationVerificationHook(Protocol):
    """Optional #86-compatible verification seam kept separate from invocation."""

    async def verify(
        self,
        request: CompensationRequest,
        action: CompletedSideEffect,
        result: CompensationResult,
    ) -> str | None: ...


class CompensationCoordinator:
    """Coordinate explicit compensation without becoming a second Plan lifecycle.

    Canonical Task/Plan/Step/Run history remains immutable. Every compensating external mutation
    goes through ``CapabilityInvoker`` and therefore through the same capability resolution,
    authorization, approval, canonical ToolInvocation binding and audit path as an ordinary call.
    """

    def __init__(
        self,
        repository: CompensationRepository,
        invoker: CapabilityInvoker,
        *,
        reconciler: CompensationReconciler | None = None,
        verification_hook: CompensationVerificationHook | None = None,
        approval_reference_lookup: ApprovalReferenceLookup | None = None,
    ) -> None:
        self.repository = repository
        self.invoker = invoker
        self.reconciler = reconciler
        self.verification_hook = verification_hook
        self.approval_reference_lookup = approval_reference_lookup

    def register_group(self, group: CompensationGroup) -> CompensationGroup:
        return self.repository.create_group(group)

    def record_completed_side_effect(self, action: CompletedSideEffect) -> CompletedSideEffect:
        group = self.repository.get_group(action.group_id)
        if (
            action.task_id != group.task_id
            or action.plan_id != group.plan_id
            or action.plan_revision != group.plan_revision
            or action.project_id != group.project_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "completed side effect does not belong to the compensation group revision",
            )
        known = {item.action_id for item in self.repository.list_actions(group.group_id)}
        missing = set(action.depends_on_action_ids) - known
        if missing:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"compensation dependencies are not completed in this group: {sorted(missing)!r}",
            )
        return self.repository.add_action(action)

    def projection(self, group_id: str) -> CompensationGroupProjection:
        group = self.repository.get_group(group_id)
        requests = {
            request.action_id: request for request in self.repository.list_requests(group_id)
        }
        actions = tuple(
            CompensationActionProjection(
                action=action,
                request=requests.get(action.action_id),
                result=(
                    None
                    if requests.get(action.action_id) is None
                    else self.repository.get_result(requests[action.action_id].compensation_id)
                ),
            )
            for action in self.repository.list_actions(group_id)
        )
        return CompensationGroupProjection(group=group, actions=actions)

    def request_compensation(
        self,
        action_id: str,
        *,
        trigger: CompensationTrigger,
        reason: str,
        actor_ref: str,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> CompensationRequest:
        action = self.repository.get_action(action_id)
        descriptor = action.compensation
        key = idempotency_key or self._default_idempotency_key(action, trigger)
        request = CompensationRequest(
            compensation_id=new_compensation_id(),
            idempotency_key=key,
            group_id=action.group_id,
            action_id=action.action_id,
            original_task_id=action.task_id,
            original_plan_id=action.plan_id,
            original_plan_revision=action.plan_revision,
            original_project_id=action.project_id,
            original_step_id=action.step_id,
            original_run_id=action.run_id,
            original_tool_invocation_id=action.tool_invocation_id,
            original_result_ref=action.original_result_ref,
            external_resource_ref=action.external_resource_ref,
            requested_capability_id=None if descriptor is None else descriptor.capability_id,
            requested_capability_version=None if descriptor is None else descriptor.version,
            trigger=trigger,
            reason=reason,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )
        stored = self.repository.create_request(request)
        if self.repository.get_result(stored.compensation_id) is not None:
            return stored
        non_compensable = action.reversibility in {
            ReversibilityClassification.IRREVERSIBLE,
            ReversibilityClassification.UNKNOWN,
        }
        if descriptor is None or non_compensable:
            self.repository.save_result(
                CompensationResult(
                    compensation_id=stored.compensation_id,
                    status=CompensationStatus.NOT_COMPENSABLE,
                    error_code=ErrorCode.UNSUPPORTED_CAPABILITY.value,
                    error_message="completed side effect has no declared compensation path",
                    manual_intervention_required=True,
                    completed_at=utc_now(),
                )
            )
            return stored
        validation_error = self._validate_compensation_evidence(action, utc_now())
        if validation_error is not None:
            status, error_code, message = validation_error
            self.repository.save_result(
                CompensationResult(
                    compensation_id=stored.compensation_id,
                    status=status,
                    error_code=error_code,
                    error_message=message,
                    manual_intervention_required=True,
                    completed_at=utc_now(),
                )
            )
        return stored

    async def execute(
        self,
        compensation_id: str,
        context: CompensationExecutionContext,
    ) -> CompensationResult:
        request = self.repository.get_request(compensation_id)
        action = self.repository.get_action(request.action_id)
        current = self.repository.get_result(compensation_id)
        if current is not None and self._is_terminal(current.status):
            return current
        if current is not None and current.status is CompensationStatus.RUNNING:
            return await self._reconcile_ambiguous(request, action, current)
        if request.requested_capability_id is None or action.compensation is None:
            return self._not_compensable(request, "compensation capability is not declared")

        started = utc_now()
        running = CompensationResult(
            compensation_id=request.compensation_id,
            status=CompensationStatus.RUNNING,
            execution_task_id=context.task_id,
            execution_run_id=context.run_id,
            execution_agent_id=context.agent_id,
            invocation_id=context.invocation_id,
            approval_id=None if current is None else current.approval_id,
            started_at=started,
        )
        self.repository.save_result(running)
        invocation = self._build_invocation(request, action, context)
        try:
            outcome = await self.invoker.invoke(invocation)
        except ContractError as exc:
            return self._record_invocation_failure(running, exc)

        if outcome.status is not InvocationStatus.SUCCEEDED:
            return self.repository.save_result(
                replace(
                    running,
                    status=CompensationStatus.FAILED,
                    provider_id=outcome.provider_id,
                    canonical_tool_invocation_id=outcome.canonical_tool_invocation_id,
                    error_code=ErrorCode.BACKEND_ERROR.value,
                    error_message=(
                        f"unexpected compensation invocation status: {outcome.status.value}"
                    ),
                    manual_intervention_required=True,
                    completed_at=utc_now(),
                )
            )
        succeeded = self.repository.save_result(
            replace(
                running,
                status=CompensationStatus.SUCCEEDED,
                provider_id=outcome.provider_id,
                canonical_tool_invocation_id=outcome.canonical_tool_invocation_id,
                result_ref=outcome.result_ref,
                artifact_refs=outcome.artifact_refs,
                evidence_refs=outcome.evidence_refs,
                completed_at=utc_now(),
            )
        )
        if self.verification_hook is None:
            return succeeded
        verification_ref = await self.verification_hook.verify(request, action, succeeded)
        if verification_ref is None:
            return succeeded
        return self.repository.save_result(replace(succeeded, verification_ref=verification_ref))

    async def compensate_group(
        self,
        group_id: str,
        *,
        trigger: CompensationTrigger,
        reason: str,
        actor_ref: str,
        correlation_id: str,
        context_factory: ExecutionContextFactory,
        current_plan_revision: int | None = None,
    ) -> CompensationGroupProjection:
        group = self.repository.get_group(group_id)
        if not self._trigger_allowed(group, trigger):
            return self.projection(group_id)
        if (
            current_plan_revision is not None
            and current_plan_revision != group.plan_revision
            and not group.policy.allow_newer_plan_revision
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "compensation group belongs to a different Plan revision",
            )

        for action in self._reverse_dependency_order(group_id):
            request = self.request_compensation(
                action.action_id,
                trigger=trigger,
                reason=reason,
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
            result = self.repository.get_result(request.compensation_id)
            if result is None or not self._is_terminal(result.status):
                context = await context_factory(request, action)
                result = await self.execute(request.compensation_id, context)
            if (
                result.status is not CompensationStatus.SUCCEEDED
                and group.policy.failure_mode is CompensationFailureMode.STOP_AND_ESCALATE
            ):
                break
        return self.projection(group_id)

    async def recover_group(
        self,
        group_id: str,
        *,
        context_factory: ExecutionContextFactory,
    ) -> CompensationGroupProjection:
        for request in self.repository.list_requests(group_id):
            result = self.repository.get_result(request.compensation_id)
            if result is not None and self._is_terminal(result.status):
                continue
            action = self.repository.get_action(request.action_id)
            if result is not None and result.status is CompensationStatus.RUNNING:
                await self._reconcile_ambiguous(request, action, result)
                continue
            context = await context_factory(request, action)
            await self.execute(request.compensation_id, context)
        return self.projection(group_id)

    async def _reconcile_ambiguous(
        self,
        request: CompensationRequest,
        action: CompletedSideEffect,
        current: CompensationResult,
    ) -> CompensationResult:
        if self.reconciler is None:
            return self.repository.save_result(
                replace(
                    current,
                    status=CompensationStatus.RECONCILIATION_REQUIRED,
                    error_code=ErrorCode.CONFLICT.value,
                    error_message=(
                        "compensation outcome is ambiguous after restart; refusing blind retry"
                    ),
                    manual_intervention_required=True,
                    completed_at=utc_now(),
                )
            )
        evidence = await self.reconciler.reconcile(request, action, current)
        if not evidence.outcome_known:
            return self.repository.save_result(
                replace(
                    current,
                    status=CompensationStatus.RECONCILIATION_REQUIRED,
                    result_ref=evidence.result_ref or current.result_ref,
                    artifact_refs=evidence.artifact_refs or current.artifact_refs,
                    evidence_refs=evidence.evidence_refs or current.evidence_refs,
                    error_code=ErrorCode.CONFLICT.value,
                    error_message=evidence.detail or "compensation outcome remains ambiguous",
                    manual_intervention_required=True,
                    completed_at=utc_now(),
                )
            )
        status = CompensationStatus.SUCCEEDED if evidence.succeeded else CompensationStatus.FAILED
        return self.repository.save_result(
            replace(
                current,
                status=status,
                result_ref=evidence.result_ref or current.result_ref,
                artifact_refs=evidence.artifact_refs or current.artifact_refs,
                evidence_refs=evidence.evidence_refs or current.evidence_refs,
                error_code=None if evidence.succeeded else ErrorCode.PERMANENT_FAILURE.value,
                error_message=None if evidence.succeeded else evidence.detail,
                manual_intervention_required=not evidence.succeeded,
                completed_at=utc_now(),
            )
        )

    def _build_invocation(
        self,
        request: CompensationRequest,
        action: CompletedSideEffect,
        context: CompensationExecutionContext,
    ) -> CapabilityInvocation:
        descriptor = action.compensation
        if descriptor is None or request.requested_capability_id is None:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "compensation descriptor is missing")
        retry_mode = (
            RetryMode.IDEMPOTENT
            if descriptor.idempotency is CompensationIdempotency.GUARANTEED
            else RetryMode.NEVER
        )
        operation = replace(
            context.operation,
            control=OperationControl(
                timeout_seconds=context.operation.control.timeout_seconds,
                idempotency_key=request.idempotency_key,
                retry_mode=retry_mode,
            ),
        )
        trace = InvocationTrace(
            correlation_id=operation.correlation_id,
            task_id=context.task_id,
            run_id=context.run_id,
            agent_id=context.agent_id,
            project_id=operation.project_id,
            causation_id=operation.causation_id,
        )
        return CapabilityInvocation(
            invocation_id=context.invocation_id,
            capability_id=request.requested_capability_id,
            version=request.requested_capability_version,
            arguments=action.compensation_arguments,
            context=operation,
            trace=trace,
            granted_permissions=context.granted_permissions,
            available_worker_capabilities=context.available_worker_capabilities,
        )

    def _record_invocation_failure(
        self,
        running: CompensationResult,
        error: ContractError,
    ) -> CompensationResult:
        approval_required = bool(error.details.get("approval_required", False))
        approval_id = running.approval_id
        if approval_required:
            status = CompensationStatus.APPROVAL_REQUIRED
            manual = False
            if self.approval_reference_lookup is not None and running.invocation_id is not None:
                approval_id = self.approval_reference_lookup(running.invocation_id)
        elif error.code in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
            status = CompensationStatus.DENIED
            manual = True
        else:
            status = CompensationStatus.FAILED
            manual = True
        canonical_id = error.details.get("canonical_tool_invocation_id")
        if not isinstance(canonical_id, str):
            canonical_id = None
        return self.repository.save_result(
            replace(
                running,
                status=status,
                provider_id=error.provider_id,
                canonical_tool_invocation_id=canonical_id,
                approval_id=approval_id,
                error_code=error.code.value,
                error_message=error.message,
                manual_intervention_required=manual,
                completed_at=utc_now(),
            )
        )

    def _not_compensable(self, request: CompensationRequest, message: str) -> CompensationResult:
        return self.repository.save_result(
            CompensationResult(
                compensation_id=request.compensation_id,
                status=CompensationStatus.NOT_COMPENSABLE,
                error_code=ErrorCode.UNSUPPORTED_CAPABILITY.value,
                error_message=message,
                manual_intervention_required=True,
                completed_at=utc_now(),
            )
        )

    def _validate_compensation_evidence(
        self,
        action: CompletedSideEffect,
        now: datetime,
    ) -> tuple[CompensationStatus, str, str] | None:
        descriptor = action.compensation
        if descriptor is None:
            return None
        missing = [
            key
            for key in descriptor.required_original_argument_keys
            if key not in action.original_arguments
        ]
        if missing:
            return (
                CompensationStatus.RECONCILIATION_REQUIRED,
                ErrorCode.CONTRACT_VIOLATION.value,
                f"required original compensation evidence is missing: {sorted(missing)!r}",
            )
        if descriptor.requires_original_result_ref and action.original_result_ref is None:
            return (
                CompensationStatus.RECONCILIATION_REQUIRED,
                ErrorCode.CONTRACT_VIOLATION.value,
                "compensation requires the original result reference",
            )
        if descriptor.window_seconds is not None:
            elapsed = (now - action.completed_at).total_seconds()
            if elapsed > descriptor.window_seconds:
                return (
                    CompensationStatus.EXPIRED,
                    ErrorCode.CONFLICT.value,
                    "declared compensation window has expired",
                )
        return None

    def _reverse_dependency_order(self, group_id: str) -> tuple[CompletedSideEffect, ...]:
        actions = self.repository.list_actions(group_id)
        by_id = {action.action_id: action for action in actions}
        indegree = {action.action_id: 0 for action in actions}
        outgoing: dict[str, list[str]] = {action.action_id: [] for action in actions}
        for action in actions:
            for predecessor in action.depends_on_action_ids:
                if predecessor not in by_id:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "compensation dependency escapes the explicit group boundary",
                    )
                indegree[action.action_id] += 1
                outgoing[predecessor].append(action.action_id)
        ready = sorted(
            (action for action in actions if indegree[action.action_id] == 0),
            key=lambda item: (item.execution_order, item.action_id),
        )
        ordered: list[CompletedSideEffect] = []
        while ready:
            action = ready.pop(0)
            ordered.append(action)
            for dependent_id in sorted(outgoing[action.action_id]):
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(by_id[dependent_id])
                    ready.sort(key=lambda item: (item.execution_order, item.action_id))
        if len(ordered) != len(actions):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "compensation group contains a dependency cycle",
            )
        return tuple(reversed(ordered))

    @staticmethod
    def _trigger_allowed(group: CompensationGroup, trigger: CompensationTrigger) -> bool:
        if trigger in {CompensationTrigger.MANUAL, CompensationTrigger.RECONCILIATION}:
            return True
        if trigger is CompensationTrigger.DOWNSTREAM_FAILURE:
            return group.policy.automation in {
                CompensationAutomation.DOWNSTREAM_FAILURE,
                CompensationAutomation.FAILURE_OR_CANCELLATION,
            }
        if trigger is CompensationTrigger.CANCELLATION:
            return group.policy.automation is CompensationAutomation.FAILURE_OR_CANCELLATION
        return False

    @staticmethod
    def _default_idempotency_key(
        action: CompletedSideEffect,
        trigger: CompensationTrigger,
    ) -> str:
        return (
            f"compensation:{action.group_id}:{action.action_id}:"
            f"plan-revision-{action.plan_revision}:{trigger.value}"
        )

    @staticmethod
    def _is_terminal(status: CompensationStatus) -> bool:
        return status in {
            CompensationStatus.SUCCEEDED,
            CompensationStatus.FAILED,
            CompensationStatus.DENIED,
            CompensationStatus.EXPIRED,
            CompensationStatus.RECONCILIATION_REQUIRED,
            CompensationStatus.NOT_COMPENSABLE,
        }
