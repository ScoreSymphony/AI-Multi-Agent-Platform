from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.learning.async_service import RuntimeGovernedLearningService
from ai_multi_agent_platform.learning.models import LearningCandidateStatus
from ai_multi_agent_platform.security import ActorIdentity


class _LearningPlatformPolicy:
    def validate_candidate(self, candidate: Any) -> None:
        return None


class _UnsupportedPromotionRegistry:
    def resolve(self, resource_type: str) -> Any:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"unsupported promotion target: {resource_type}",
        )


class _UnexpectedLearningOffload:
    async def run(self, operation: Any, *, message: str) -> Any:
        raise AssertionError("persistence or approval preparation ran before capability validation")


def test_learning_rejects_unsupported_target_before_governance_side_effects() -> None:
    candidate = SimpleNamespace(
        status=LearningCandidateStatus.ACCEPTED,
        project_id=None,
        target=SimpleNamespace(resource_type="unsupported-target"),
    )

    async def get_candidate(candidate_id: str) -> Any:
        return candidate

    service = SimpleNamespace(
        async_get_candidate=get_candidate,
        platform_policy=_LearningPlatformPolicy(),
        _require_expected_revision=lambda current, expected: None,
        promotion_registry=_UnsupportedPromotionRegistry(),
        _persistence_offload=_UnexpectedLearningOffload(),
    )
    actor = cast(ActorIdentity, SimpleNamespace())
    operation = cast(OperationContext, SimpleNamespace(project_id=None))

    async def scenario() -> None:
        with pytest.raises(ContractError) as caught:
            await RuntimeGovernedLearningService.promote(
                service,
                "learning_candidate_test",
                actor=actor,
                operation=operation,
            )
        assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    asyncio.run(scenario())
