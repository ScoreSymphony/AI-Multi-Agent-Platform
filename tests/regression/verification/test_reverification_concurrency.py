from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from ai_multi_agent_platform.verification.async_persistence import (
    AsyncVerificationCompletionAuthorityAdapter,
)
from ai_multi_agent_platform.verification.gate import VerificationCompletionAuthority
from ai_multi_agent_platform.verification.models import VerificationRequest, VerificationSubject


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
