from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    BudgetState,
    InMemoryUsageStore,
    UsageBudget,
    UsageBudgetResourceService,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.organizations.accounting import OrganizationUsageBudgetResourceService


class _ChangingAccountingService(AccountingService):
    def __init__(self, listed: UsageBudget, current: UsageBudget) -> None:
        super().__init__(InMemoryUsageStore())
        self._listed = listed
        self._current = current

    def get_budget(self, budget_id: str) -> UsageBudget | None:
        assert budget_id == self._listed.id
        return self._listed

    def budget_state(self, budget_id: str) -> BudgetState:
        assert budget_id == self._listed.id
        return BudgetState(
            budget=self._current,
            consumed=25.0,
            remaining=75.0,
            fraction=0.25,
            level=None,
        )


class _Visibility:
    def __init__(self) -> None:
        self.seen: list[UsageBudget] = []

    async def budget_visible(self, context: RequestContext, budget: UsageBudget) -> bool:
        del context
        self.seen.append(budget)
        return budget.owner_id == "owner-old"


def _budgets() -> tuple[UsageBudget, UsageBudget]:
    old = UsageBudget(
        metric_type="storage.bytes",
        unit="bytes",
        scope_type="project",
        scope_id="project-old",
        limit=100.0,
        owner_type="user",
        owner_id="owner-old",
    )
    current = replace(
        old,
        scope_id="project-new",
        owner_id="owner-new",
        version=2,
    )
    return old, current


def _old_owner_context() -> RequestContext:
    return RequestContext(
        request_id="request-budget-reauthorization",
        correlation_id="request-budget-reauthorization",
        actor=ActorContext(
            principal_ref="user:owner-old",
            owner_type="user",
            owner_id="owner-old",
        ),
    )


def test_budget_resource_reauthorizes_current_budget_state() -> None:
    old, current = _budgets()
    resource = UsageBudgetResourceService(_ChangingAccountingService(old, current))

    async def scenario() -> None:
        with pytest.raises(ContractError) as failure:
            await resource.get_resource(_old_owner_context(), old.id)
        assert failure.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_organization_budget_resource_reauthorizes_current_budget_state() -> None:
    old, current = _budgets()
    visibility = _Visibility()
    resource = OrganizationUsageBudgetResourceService(
        _ChangingAccountingService(old, current),
        visibility,  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        with pytest.raises(ContractError) as failure:
            await resource.get_resource(_old_owner_context(), old.id)
        assert failure.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
    assert visibility.seen == [old, current]
