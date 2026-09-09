"""Completion hardening for canonical compensation execution.

This coordinator preserves the original #596 service contract while closing recovery and security
gaps discovered during integration review: legacy idempotency-key recovery, execution-time expiry
enforcement and invocation-scoped Approval requirements.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ai_multi_agent_platform.capabilities import CapabilityInvocation
from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import (
    CompensationActionProjection,
    CompensationExecutionContext,
    CompensationGroupProjection,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    utc_now,
)
from .service import (
    CompensationCoordinator as _BaseCompensationCoordinator,
    ExecutionContextFactory,
)


class CompensationCoordinator(_BaseCompensationCoordinator):
    """Hardened #596 coordinator used by the public compensation package.

    The base service remains the canonical implementation of group ordering, reconciliation,
    Capability ownership and result recording. This subclass only strengthens safety at boundaries
    that need cross-version or execution-time context.
    """

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
        if idempotency_key is None:
            action = self.repository.get_action(action_id)
            canonical = self.repository.find_request_by_key(self._default_idempotency_key(action))
            if canonical is not None:
                self._validate_request_target(canonical, action)
            legacy = self._find_legacy_request(action)
            if (
                canonical is not None
                and legacy is not None
                and canonical.compensation_id != legacy.compensation_id
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "canonical and legacy compensation requests coexist for one immutable target; "
                    "manual reconciliation is required",
                )
            if canonical is None and legacy is not None:
                return legacy
        return super().request_compensation(
            action_id,
            trigger=trigger,
            reason=reason,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

    def projection(self, group_id: str) -> CompensationGroupProjection:
        """Prefer unresolved reconciliation state over a newer sibling request in read models."""

        group = self.repository.get_group(group_id)
        requests_by_action: dict[str, list[CompensationRequest]] = {}
        for request in self.repository.list_requests(group_id):
            requests_by_action.setdefault(request.action_id, []).append(request)

        actions: list[CompensationActionProjection] = []
        for action in self.repository.list_actions(group_id):
            request = self._select_projection_request(
                requests_by_action.get(action.action_id, [])
            )
            result = (
                None if request is None else self.repository.get_result(request.compensation_id)
            )
            actions.append(
                CompensationActionProjection(
                    action=action,
                    request=request,
                    result=result,
                )
            )
        return CompensationGroupProjection(group=group, actions=tuple(actions))

    async def execute(
        self,
        compensation_id: str,
        context: CompensationExecutionContext,
    ) -> CompensationResult:
        request = self.repository.get_request(compensation_id)
        action = self.repository.get_action(request.action_id)
        current = self.repository.get_result(compensation_id)

        # Terminal results stay immutable. Recovery must nevertheless refuse to execute a sibling
        # request when an upgraded store contains multiple persisted identities for the same
        # immutable compensation target.
        if current is not None and self._is_terminal(current.status):
            return current
        identity_conflict = self._request_identity_conflict(action)
        if identity_conflict is not None:
            checked_at = utc_now()
            if current is not None:
                return self.repository.save_result(
                    replace(
                        current,
                        status=CompensationStatus.RECONCILIATION_REQUIRED,
                        error_code=identity_conflict.code.value,
                        error_message=identity_conflict.message,
                        manual_intervention_required=True,
                        completed_at=checked_at,
                    )
                )
            return self.repository.save_result(
                CompensationResult(
                    compensation_id=request.compensation_id,
                    status=CompensationStatus.RECONCILIATION_REQUIRED,
                    execution_task_id=context.task_id,
                    execution_run_id=context.run_id,
                    execution_agent_id=context.agent_id,
                    invocation_id=context.invocation_id,
                    error_code=identity_conflict.code.value,
                    error_message=identity_conflict.message,
                    manual_intervention_required=True,
                    completed_at=checked_at,
                )
            )

        if current is not None and current.status is CompensationStatus.RUNNING:
            return await super().execute(compensation_id, context)

        # Fail early when the window is already closed, while preserving any prior governed
        # invocation linkage (for example an APPROVAL_REQUIRED result). CapabilityInvoker repeats
        # the deadline check after all asynchronous governance and immediately before the provider.
        checked_at = utc_now()
        validation_error = self._validate_compensation_evidence(action, checked_at)
        if validation_error is not None:
            status, error_code, message = validation_error
            if current is not None:
                return self.repository.save_result(
                    replace(
                        current,
                        status=status,
                        error_code=error_code,
                        error_message=message,
                        manual_intervention_required=True,
                        completed_at=checked_at,
                    )
                )
            return self.repository.save_result(
                CompensationResult(
                    compensation_id=request.compensation_id,
                    status=status,
                    execution_task_id=context.task_id,
                    execution_run_id=context.run_id,
                    execution_agent_id=context.agent_id,
                    invocation_id=context.invocation_id,
                    error_code=error_code,
                    error_message=message,
                    manual_intervention_required=True,
                    completed_at=checked_at,
                )
            )

        result = await super().execute(compensation_id, context)
        if (
            result.status is CompensationStatus.EXPIRED
            and current is not None
            and current.status is CompensationStatus.APPROVAL_REQUIRED
            and current.canonical_tool_invocation_id is not None
        ):
            # The base executor creates a fresh RUNNING result for the retry. If asynchronous
            # governance crosses the deadline, keep the previously governed Approval/ToolInvocation
            # tuple together instead of pairing the old Approval with the retry invocation.
            return self.repository.save_result(
                replace(
                    current,
                    status=CompensationStatus.EXPIRED,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    manual_intervention_required=True,
                    completed_at=result.completed_at,
                )
            )
        return result

    async def recover_group(
        self,
        group_id: str,
        *,
        context_factory: ExecutionContextFactory,
    ) -> CompensationGroupProjection:
        """Recover persisted work while validating mixed identities before RUNNING reconciliation."""

        for request in self.repository.list_requests(group_id):
            result = self.repository.get_result(request.compensation_id)
            if result is not None and self._is_terminal(result.status):
                continue
            action = self.repository.get_action(request.action_id)
            if result is not None and result.status is CompensationStatus.RUNNING:
                identity_conflict = self._request_identity_conflict(action)
                if identity_conflict is not None:
                    self.repository.save_result(
                        replace(
                            result,
                            status=CompensationStatus.RECONCILIATION_REQUIRED,
                            error_code=identity_conflict.code.value,
                            error_message=identity_conflict.message,
                            manual_intervention_required=True,
                            completed_at=utc_now(),
                        )
                    )
                else:
                    await self._reconcile_ambiguous(request, action, result)
                continue
            context = await context_factory(request, action)
            await self.execute(request.compensation_id, context)
        return self.projection(group_id)

    def _build_invocation(
        self,
        request: CompensationRequest,
        action: CompletedSideEffect,
        context: CompensationExecutionContext,
    ) -> CapabilityInvocation:
        invocation = super()._build_invocation(request, action, context)
        descriptor = action.compensation
        group = self.repository.get_group(action.group_id)
        require_approval = group.policy.require_human_approval or bool(
            descriptor is not None and descriptor.requires_approval
        )
        expires_at = None
        if descriptor is not None and descriptor.window_seconds is not None:
            expires_at = action.completed_at + timedelta(seconds=descriptor.window_seconds)
        return CapabilityInvocation(
            invocation_id=invocation.invocation_id,
            capability_id=invocation.capability_id,
            arguments=invocation.arguments,
            context=invocation.context,
            trace=invocation.trace,
            version=invocation.version,
            compatibility=invocation.compatibility,
            granted_permissions=invocation.granted_permissions,
            available_worker_capabilities=invocation.available_worker_capabilities,
            require_approval=require_approval,
            expires_at=expires_at,
        )

    def _record_invocation_failure(
        self,
        running: CompensationResult,
        error: ContractError,
    ) -> CompensationResult:
        if bool(error.details.get("invocation_expired", False)):
            canonical_id = error.details.get("canonical_tool_invocation_id")
            if not isinstance(canonical_id, str):
                canonical_id = None
            return self.repository.save_result(
                replace(
                    running,
                    status=CompensationStatus.EXPIRED,
                    provider_id=error.provider_id,
                    canonical_tool_invocation_id=canonical_id,
                    error_code=error.code.value,
                    error_message=error.message,
                    manual_intervention_required=True,
                    completed_at=utc_now(),
                )
            )
        return super()._record_invocation_failure(running, error)

    def _request_identity_conflict(self, action: CompletedSideEffect) -> ContractError | None:
        """Return a fail-closed conflict when persisted default request identities disagree."""

        canonical_key = self._default_idempotency_key(action)
        matches: list[CompensationRequest] = []
        canonical = self.repository.find_request_by_key(canonical_key)
        if canonical is not None:
            self._validate_request_target(canonical, action)
            matches.append(canonical)
        for legacy_trigger in CompensationTrigger:
            existing = self.repository.find_request_by_key(
                f"{canonical_key}:{legacy_trigger.value}"
            )
            if existing is not None:
                self._validate_request_target(existing, action)
                matches.append(existing)

        unique_ids = {request.compensation_id for request in matches}
        if len(unique_ids) <= 1:
            return None
        return ContractError(
            ErrorCode.CONFLICT,
            "canonical and legacy compensation requests coexist for one immutable target; "
            "manual reconciliation is required",
        )

    def _find_legacy_request(self, action: CompletedSideEffect) -> CompensationRequest | None:
        """Resolve pre-hardening trigger-suffixed keys without creating a second undo.

        The first #596 implementation persisted keys ending in ``:<trigger>``. After the canonical
        identity became trigger-independent, an upgraded SQLite store could otherwise miss the old
        request and repeat a destructive external compensation. Multiple historical requests for
        one target are treated as an ambiguity requiring manual reconciliation rather than guessed.
        """

        canonical_key = self._default_idempotency_key(action)
        matches: list[CompensationRequest] = []
        for legacy_trigger in CompensationTrigger:
            existing = self.repository.find_request_by_key(
                f"{canonical_key}:{legacy_trigger.value}"
            )
            if existing is not None:
                self._validate_request_target(existing, action)
                matches.append(existing)

        unique = {request.compensation_id: request for request in matches}
        if len(unique) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "multiple legacy compensation requests exist for one immutable target; "
                "manual reconciliation is required",
            )
        return next(iter(unique.values()), None)

    def _select_projection_request(
        self,
        requests: list[CompensationRequest],
    ) -> CompensationRequest | None:
        if not requests:
            return None
        selected = requests[-1]
        for request in requests:
            result = self.repository.get_result(request.compensation_id)
            if (
                result is not None
                and result.status is CompensationStatus.RECONCILIATION_REQUIRED
                and result.manual_intervention_required
            ):
                selected = request
        return selected

    @staticmethod
    def _validate_request_target(
        request: CompensationRequest,
        action: CompletedSideEffect,
    ) -> None:
        descriptor = action.compensation
        expected_capability_id = None if descriptor is None else descriptor.capability_id
        expected_capability_version = None if descriptor is None else descriptor.version
        same_target = (
            request.group_id == action.group_id
            and request.action_id == action.action_id
            and request.original_task_id == action.task_id
            and request.original_plan_id == action.plan_id
            and request.original_plan_revision == action.plan_revision
            and request.original_project_id == action.project_id
            and request.original_step_id == action.step_id
            and request.original_run_id == action.run_id
            and request.original_tool_invocation_id == action.tool_invocation_id
            and request.original_result_ref == action.original_result_ref
            and request.external_resource_ref == action.external_resource_ref
            and request.requested_capability_id == expected_capability_id
            and request.requested_capability_version == expected_capability_version
        )
        if not same_target:
            raise ContractError(
                ErrorCode.CONFLICT,
                "compensation idempotency key belongs to another immutable target",
            )


__all__ = ["CompensationCoordinator"]
