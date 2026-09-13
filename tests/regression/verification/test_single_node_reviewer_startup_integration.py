from __future__ import annotations

import asyncio

from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.durable_connectors import build_single_node_deployment
from ai_multi_agent_platform.deployment.server import _run_startup_recovery
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
    ReviewerRecoveryRecord,
)


class CountingReviewerReconciler:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile_startup(self) -> tuple[ReviewerRecoveryRecord, ...]:
        self.calls += 1
        return ()


def test_durable_single_node_server_startup_reaches_reviewer_reconciliation(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        assert isinstance(deployment.reviewer_recovery, AutomaticReviewerStartupReconciler)

        counter = CountingReviewerReconciler()
        deployment.reviewer_recovery = counter  # type: ignore[assignment]

        result = await _run_startup_recovery(deployment)

        assert counter.calls == 1
        assert result.reviewer_recoveries == ()
        assert result.blocked_verification_ids == ()
        assert result.ready_for_service is True

    asyncio.run(scenario())
