"""Budget-enforced runtime decorators for canonical model and capability boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    ModelRequest,
    ModelResponse,
)
from ai_multi_agent_platform.contracts.model_stream import ModelStreamEvent

from .models import BudgetActionKind, BudgetAdmissionDecision, BudgetDimension
from .service import TaskBudgetAdmission


class _ModelRuntimeBoundary(Protocol):
    """Structural model-runtime seam needed by the budget decorator."""

    registry: Any
    router: Any
    egress_gate: Any

    async def select(self, request: ModelRequest) -> Any: ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...

    async def generate_canonical(self, request: Any) -> Any: ...

    def stream_canonical(self, request: Any) -> AsyncIterator[ModelStreamEvent]: ...


class TaskBudgetModelRuntime:
    """Decorate the canonical ModelRuntime so autonomous callers cannot skip #902 admission."""

    def __init__(self, inner: _ModelRuntimeBoundary, budgets: TaskBudgetAdmission) -> None:
        self._inner = inner
        self._budgets = budgets
        # Preserve the operational attributes consumed by existing composition code.
        self.registry = inner.registry
        self.router = inner.router
        self.egress_gate = inner.egress_gate

    def __getattr__(self, name: str) -> Any:
        """Keep the decorator transparent for operational attributes owned by the inner runtime."""

        return getattr(self._inner, name)

    async def select(self, request: ModelRequest):  # type: ignore[no-untyped-def]
        return await self._inner.select(request)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        decision = await self._admit_contract(request)
        if decision is None:
            return await self._inner.generate(request)
        completed = False
        try:
            response = await self._inner.generate(request)
            completed = True
        finally:
            if not completed:
                await self._budgets.release(decision)
        await self._reconcile(decision)
        return response

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            decision = await self._admit_contract(request)
            if decision is None:
                async for event in self._inner.stream(request):
                    yield event
                return
            completed = False
            try:
                async for event in self._inner.stream(request):
                    yield event
                completed = True
            finally:
                if not completed:
                    await self._budgets.release(decision)
            await self._reconcile(decision)

        return iterate()

    async def generate_canonical(self, request: Any) -> Any:
        decision = await self._admit(
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
            correlation_id=request.context.correlation_id,
            causation_id=request.context.causation_id,
        )
        if decision is None:
            return await self._inner.generate_canonical(request)
        completed = False
        try:
            response = await self._inner.generate_canonical(request)
            completed = True
        finally:
            if not completed:
                await self._budgets.release(decision)
        await self._reconcile(decision)
        return response

    def stream_canonical(self, request: Any) -> AsyncIterator[ModelStreamEvent]:
        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            decision = await self._admit(
                task_id=request.task_id,
                run_id=request.run_id,
                agent_id=request.agent_id,
                correlation_id=request.context.correlation_id,
                causation_id=request.context.causation_id,
            )
            if decision is None:
                async for event in self._inner.stream_canonical(request):
                    yield event
                return
            completed = False
            try:
                async for event in self._inner.stream_canonical(request):
                    yield event
                completed = True
            finally:
                if not completed:
                    await self._budgets.release(decision)
            await self._reconcile(decision)

        return iterate()

    async def _admit_contract(self, request: ModelRequest) -> BudgetAdmissionDecision | None:
        return await self._admit(
            task_id=_requirement_ref(request.requirements, "task_id"),
            run_id=_requirement_ref(request.requirements, "run_id"),
            agent_id=_requirement_ref(request.requirements, "agent_id"),
            correlation_id=request.context.correlation_id,
            causation_id=request.context.causation_id,
        )

    async def _admit(
        self,
        *,
        task_id: str | None,
        run_id: str | None,
        agent_id: str | None,
        correlation_id: str | None,
        causation_id: str | None,
    ) -> BudgetAdmissionDecision | None:
        if task_id is None:
            return None
        decision = await self._budgets.admit(
            task_id=task_id,
            action=BudgetActionKind.MODEL_CALL,
            quantities={BudgetDimension.MODEL_CALLS: 1.0},
            run_id=run_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            provenance={"enforcement_point": "model_runtime"},
        )
        _require_budget_permitted(decision)
        return decision

    async def _reconcile(self, decision: BudgetAdmissionDecision) -> None:
        post_action = await self._budgets.reconcile(decision)
        _require_budget_permitted(post_action)


class TaskBudgetCapabilityInvoker:
    """Decorate the canonical CapabilityInvoker at the provider-execution boundary."""

    def __init__(self, inner: Any, budgets: TaskBudgetAdmission) -> None:
        self._inner = inner
        self._budgets = budgets
        # Preserve the operational egress surface consumed by public deployment composition/tests.
        self.egress_gate = getattr(inner, "egress_gate", None)

    def __getattr__(self, name: str) -> Any:
        """Delegate non-budget operational seams to the canonical wrapped invoker."""

        return getattr(self._inner, name)

    async def invoke(self, request: Any) -> Any:
        task_id = request.trace.task_id
        if task_id is None:
            return await self._inner.invoke(request)
        decision = await self._budgets.admit(
            task_id=task_id,
            action=BudgetActionKind.TOOL_CALL,
            quantities={BudgetDimension.TOOL_CALLS: 1.0},
            run_id=request.trace.run_id,
            step_id=None,
            agent_id=request.trace.agent_id,
            agent_run_id=request.trace.agent_run_id,
            correlation_id=request.context.correlation_id,
            causation_id=request.context.causation_id,
            provenance={"enforcement_point": "capability_invoker"},
        )
        _require_budget_permitted(decision)
        completed = False
        try:
            result = await self._inner.invoke(request)
            completed = True
        finally:
            if not completed:
                await self._budgets.release(decision)
        post_action = await self._budgets.reconcile(decision)
        _require_budget_permitted(post_action)
        return result


def as_model_runtime(runtime: TaskBudgetModelRuntime) -> Any:
    """Compatibility seam for composition sites typed to the concrete model runtime."""

    return runtime


def as_capability_invoker(invoker: TaskBudgetCapabilityInvoker) -> Any:
    """Compatibility seam for the public egress-enforced Agent invoker annotation."""

    return invoker


def _requirement_ref(requirements: Mapping[str, JsonValue], name: str) -> str | None:
    value = requirements.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _require_budget_permitted(decision: BudgetAdmissionDecision) -> None:
    if decision.permitted:
        return
    raise ContractError(
        ErrorCode.RESOURCE_EXHAUSTED,
        decision.reason,
        details={
            "budget_outcome": decision.outcome.value,
            "budget_action": decision.action.value,
            "task_id": decision.task_id,
            "blocking_dimension": (
                None if decision.blocking_dimension is None else decision.blocking_dimension.value
            ),
        },
    )


__all__ = [
    "TaskBudgetCapabilityInvoker",
    "TaskBudgetModelRuntime",
    "as_capability_invoker",
    "as_model_runtime",
]
