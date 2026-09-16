from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution.budgets import (
    BudgetConsumptionSource,
    BudgetDimension,
    TaskBudgetLimit,
    TaskBudgetPolicy,
)
from ai_multi_agent_platform.execution.budgets.control_plane import (
    TASK_BUDGET_COLLECTION,
    TASK_BUDGET_CONFIGURE_COMMAND,
    TASK_BUDGET_MODULE,
    TASK_BUDGET_REVISE_COMMAND,
)
from ai_multi_agent_platform.execution.budgets.models import utc_now


def test_public_single_node_profile_persists_task_budget_authority_across_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        task_id = new_id("task")
        first = build_single_node_deployment(config)

        assert first.accounting_service is not None
        assert TASK_BUDGET_MODULE in first.control_plane.registered_modules
        assert first.control_plane.resource_owner(TASK_BUDGET_COLLECTION) == TASK_BUDGET_MODULE
        assert (
            first.control_plane.command_owner(TASK_BUDGET_CONFIGURE_COMMAND) == TASK_BUDGET_MODULE
        )
        assert first.control_plane.command_owner(TASK_BUDGET_REVISE_COMMAND) == TASK_BUDGET_MODULE
        assert TASK_BUDGET_COLLECTION in first.control_plane.extension_collections
        assert TASK_BUDGET_CONFIGURE_COMMAND in first.control_plane.extension_commands
        assert TASK_BUDGET_REVISE_COMMAND in first.control_plane.extension_commands

        await first.task_budgets.put_policy(
            TaskBudgetPolicy(
                task_id=task_id,
                started_at=utc_now(),
                limits=(
                    TaskBudgetLimit(
                        dimension=BudgetDimension.REPLANS,
                        limit=2.0,
                        source=BudgetConsumptionSource.RUNTIME_COUNTER,
                    ),
                ),
            )
        )

        restarted = build_single_node_deployment(config)
        policy = await restarted.task_budgets.policy(task_id)

        assert restarted.accounting_service is not None
        assert policy is not None
        assert policy.limits[0].dimension is BudgetDimension.REPLANS
        assert policy.limits[0].limit == 2.0
        assert (config.database_dir / "accounting.sqlite3").exists()
        assert (config.database_dir / "task-execution-budgets.sqlite3").exists()

    asyncio.run(scenario())
