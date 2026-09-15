from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ai_multi_agent_platform.agents import AgentRepository
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    DurableConsumedHandoffContextAdapter,
    HandoffRepository,
)
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.kernel.repository import CommandRecord
from ai_multi_agent_platform.kernel.run_commands import KernelRunCommands, RunCommandKernelHost
from ai_multi_agent_platform.learning.async_service import RuntimeGovernedLearningService
from ai_multi_agent_platform.learning.models import LearningCandidateStatus
from ai_multi_agent_platform.security import ActorIdentity
from ai_multi_agent_platform.verification.async_persistence import (
    AsyncVerificationCompletionAuthorityAdapter,
)
from ai_multi_agent_platform.verification.gate import VerificationCompletionAuthority
from ai_multi_agent_platform.verification.models import VerificationRequest, VerificationSubject


class _AttachmentHost:
    def __init__(self) -> None:
        self.invalidation_started = asyncio.Event()
        self.release_invalidation = asyncio.Event()
        self.committed = asyncio.Event()
        self.task_state = cast(TaskState, object())

    async def get_task(self, task_id: str) -> TaskState:
        return self.task_state

    async def _task_command(
        self,
        task_id: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None:
        return None

    async def _invalidate_completion_subject(self, task_id: str) -> None:
        self.invalidation_started.set()
        await self.release_invalidation.wait()

    async def _commit_task_command(self, **kwargs: Any) -> CommandRecord:
        self.committed.set()
        return cast(CommandRecord, object())


@pytest.mark.parametrize("kind", ["artifact", "result"])
def test_output_attachment_settles_after_cancellation_during_invalidation(kind: str) -> None:
    async def scenario() -> None:
        host = _AttachmentHost()
        commands = KernelRunCommands(cast(RunCommandKernelHost, host))
        task_id = new_id("task")
        if kind == "artifact":
            operation = asyncio.create_task(
                commands.attach_artifact(
                    idempotency_key="attach-cancel-artifact",
                    task_id=task_id,
                    artifact_id=new_id("artifact"),
                )
            )
        else:
            operation = asyncio.create_task(
                commands.attach_result(
                    idempotency_key="attach-cancel-result",
                    task_id=task_id,
                    result_id=new_id("result"),
                )
            )

        await host.invalidation_started.wait()
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        assert not host.committed.is_set()

        host.release_invalidation.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert host.committed.is_set()

    asyncio.run(scenario())


def test_durable_handoff_adapter_preserves_third_positional_adapter_id() -> None:
    repository = cast(HandoffRepository, object())
    agents = cast(AgentRepository, object())

    adapter = DurableConsumedHandoffContextAdapter(repository, agents, "custom-adapter")

    assert adapter.adapter_id == "custom-adapter"
    assert adapter.runtime_repository is None


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


class _VerificationStore:
    def __init__(self) -> None:
        self.verifier_kind = object()
        self.previous = SimpleNamespace(
            verification_id="verification_previous",
            task_id="task_verification",
            policy_id="verification_policy",
            policy_version=1,
            stage_id="review",
            repair_attempt=0,
            requested_verifier_kind=self.verifier_kind,
        )
        self.requests: list[Any] = [self.previous]

    def get_request(self, verification_id: str) -> Any:
        return next(
            request for request in self.requests if request.verification_id == verification_id
        )

    def history(self, *, task_id: str) -> tuple[tuple[Any, None], ...]:
        return tuple(
            (request, None) for request in self.requests if request.task_id == task_id
        )


class _VerificationCompletion:
    def __init__(self) -> None:
        self.verification = _VerificationStore()
        self.create_calls = 0

    def request_canonical_reverification_after_repair(
        self,
        verification_id: str,
        *,
        new_subject: VerificationSubject,
        correlation_id: str,
        run_id: str | None = None,
        result_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: Any = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        self.create_calls += 1
        previous = self.verification.previous
        request = SimpleNamespace(
            verification_id=f"verification_child_{self.create_calls}",
            task_id=previous.task_id,
            policy_id=previous.policy_id,
            policy_version=previous.policy_version,
            stage_id=previous.stage_id,
            repair_attempt=previous.repair_attempt + 1,
            requested_verifier_kind=previous.requested_verifier_kind,
            subject=new_subject,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id,
            capability_ids=capability_ids,
            producer=producer,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self.verification.requests.append(request)
        return cast(VerificationRequest, request)


def test_concurrent_reverification_reuses_one_canonical_child() -> None:
    completion = _VerificationCompletion()
    adapter = AsyncVerificationCompletionAuthorityAdapter(
        cast(VerificationCompletionAuthority, completion)
    )
    subject = cast(
        VerificationSubject,
        SimpleNamespace(subject_id="result_repaired"),
    )

    async def scenario() -> None:
        first, second = await asyncio.gather(
            adapter.request_canonical_reverification_after_repair(
                "verification_previous",
                new_subject=subject,
                correlation_id="correlation_repair",
                run_id="run_repair",
                result_id="result_repaired",
                artifact_ids=("artifact_repaired",),
                project_id="project_repair",
                capability_ids=("capability_repair",),
                causation_id="repair-cause",
            ),
            adapter.request_canonical_reverification_after_repair(
                "verification_previous",
                new_subject=subject,
                correlation_id="correlation_repair",
                run_id="run_repair",
                result_id="result_repaired",
                artifact_ids=("artifact_repaired",),
                project_id="project_repair",
                capability_ids=("capability_repair",),
                causation_id="repair-cause",
            ),
        )
        assert first is second
        assert completion.create_calls == 1

    asyncio.run(scenario())
