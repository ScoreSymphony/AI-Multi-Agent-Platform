"""Composition helpers that make the planner unable to execute canonical Runs."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    HealthStatus,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)


class PlanningOnlyLifecycleBackend(LifecycleBackend):
    """Kernel construction dependency that deliberately rejects all execution operations.

    #439 needs the existing ``PlatformKernel.plan_task`` path to allocate canonical Plan/Step
    identities. It must not gain a second execution path as a side effect. Public single-node
    composition therefore gives the planning kernel this backend: plan mutation remains available,
    while Run start/read/cancel through that kernel fails closed.
    """

    descriptor = ProviderDescriptor(
        provider_id="planning-only-lifecycle",
        provider_type="planning-boundary",
        supported_operations=(),
        capabilities=(
            Capability(
                name="planning.execution.forbidden",
                kind=CapabilityKind.EXECUTION,
                supported_operations=(),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        del request
        raise _execution_forbidden()

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del run_id, context
        raise _execution_forbidden()

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del run_id, context
        raise _execution_forbidden()


def _execution_forbidden() -> ContractError:
    return ContractError(
        ErrorCode.FORBIDDEN,
        "autonomous planning cannot execute or control canonical Runs",
        provider_id=PlanningOnlyLifecycleBackend.descriptor.provider_id,
    )
