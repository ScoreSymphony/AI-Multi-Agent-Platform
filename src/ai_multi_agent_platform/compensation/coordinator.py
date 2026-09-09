"""Completion hardening for canonical compensation execution.

This coordinator preserves the original #596 service contract while closing recovery and security
gaps discovered during integration review: legacy idempotency-key recovery, execution-time expiry
enforcement and invocation-scoped Approval requirements.
"""

from __future__ import annotations

from ai_multi_agent_platform.capabilities import CapabilityInvocation
from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import (
    CompensationExecutionContext,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    utc_now,
)
from .service import CompensationCoordinator as _BaseCompensationCoordinator


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

    async def execute(
        self,
        compensation_id: str,
        context: CompensationExecutionContext,
    ) -> CompensationResult:
        request = self.repository.get_request(compensation_id)
        action = self.repository.get_action(request.action_id)
        current = self.repository.get_result(compensation_id)

        # Terminal results stay immutable, while an ambiguous RUNNING result must go through the
        # base reconciler because the external compensation may already have happened.
        if current is not None and self._is_terminal(current.status):
            return current
        if current is not None and current.status is CompensationStatus.RUNNING:
            return await super().execute(compensation_id, context)

        # Re-check all time-sensitive evidence immediately before provider execution. In
        # particular, a request that waited for Approval must not run after its declared window.
        checked_at = utc_now()
        validation_error = self._validate_compensation_evidence(action, checked_at)
        if validation_error is not None:
            status, error_code, message = validation_error
            return self.repository.save_result(
                CompensationResult(
                    compensation_id=request.compensation_id,
                    status=status,
                    execution_task_id=context.task_id,
                    execution_run_id=context.run_id,
                    execution_agent_id=context.agent_id,
                    invocation_id=context.invocation_id,
                    approval_id=None if current is None else current.approval_id,
                    error_code=error_code,
                    error_message=message,
                    manual_intervention_required=True,
                    completed_at=checked_at,
                )
            )

        return await super().execute(compensation_id, context)

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
