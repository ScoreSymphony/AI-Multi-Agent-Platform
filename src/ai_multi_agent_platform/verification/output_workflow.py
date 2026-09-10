"""Productive output-to-review coordination for automatic Agent Verification (#711).

The kernel remains lifecycle authority and canonical Verification remains review authority.
This integration composes those existing seams so a configured kernel output attachment can
drive Agent review without callers manually creating VerificationRequest/runtime plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from ai_multi_agent_platform.agents import AgentRunRecord
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import TaskStatus, validate_id
from ai_multi_agent_platform.kernel import OutputAttachmentObserver, PlatformKernel
from ai_multi_agent_platform.kernel.models import TaskState

from .agent_workflow import (
    AutomaticReviewerWorkflow,
    ReviewerRuntimeOptions,
    ReviewWorkflowResult,
)
from .evidence import CanonicalVerificationRuntime
from .gate import VerificationCompletionAuthority
from .models import (
    CompletionState,
    VerificationPolicy,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerificationSubject,
    VerifierKind,
)

_OutputType = Literal["result", "artifact"]
_SOURCE = "automatic-reviewer-output-workflow"


@dataclass(frozen=True, slots=True)
class AutomaticOutputReviewResult:
    """Canonical output/review state after one productive attachment workflow."""

    task: TaskState
    subject: VerificationSubject | None
    reviews: tuple[ReviewWorkflowResult, ...]

    @property
    def reviewer_runs(self) -> tuple[AgentRunRecord, ...]:
        return tuple(
            cycle.reviewer_run
            for review in self.reviews
            for cycle in review.cycles
            if cycle.reviewer_run is not None
        )


class AutomaticReviewerOutputCoordinator:
    """Attach canonical output and drive every configured Agent-verifier stage.

    This is the Verification-side integration seam for execution components that have produced a
    canonical Result/Artifact. It does not embed review policy into AgentRuntime or kernel state:
    the kernel attaches output, Verification resolves the exact subject, and
    AutomaticReviewerWorkflow dispatches only AGENT stages required by the Task policy.
    """

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        runtime: CanonicalVerificationRuntime,
        completion: VerificationCompletionAuthority,
        reviewer: AutomaticReviewerWorkflow,
    ) -> None:
        self._kernel = kernel
        self._runtime = runtime
        self._completion = completion
        self._reviewer = reviewer

    async def attach_result_and_review(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        result_id: str,
        run_id: str | None = None,
        actor_ref: str | None = None,
        options: ReviewerRuntimeOptions | None = None,
    ) -> AutomaticOutputReviewResult:
        """Legacy explicit composition helper; normal configured flow uses the kernel observer."""

        validate_id(result_id, "result")
        await self._kernel.attach_result(
            idempotency_key=idempotency_key,
            task_id=task_id,
            result_id=result_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=_SOURCE,
        )
        return await self.review_attached_subject(
            task_id=task_id,
            subject_type="result",
            subject_id=result_id,
            correlation_id=task_id,
            causation_id=idempotency_key,
            actor_ref=actor_ref,
            options=options,
        )

    async def attach_artifact_and_review(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        artifact_id: str,
        run_id: str | None = None,
        actor_ref: str | None = None,
        options: ReviewerRuntimeOptions | None = None,
    ) -> AutomaticOutputReviewResult:
        """Legacy explicit composition helper; normal configured flow uses the kernel observer."""

        validate_id(artifact_id, "artifact")
        await self._kernel.attach_artifact(
            idempotency_key=idempotency_key,
            task_id=task_id,
            artifact_id=artifact_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=_SOURCE,
        )
        return await self.review_attached_subject(
            task_id=task_id,
            subject_type="artifact",
            subject_id=artifact_id,
            correlation_id=task_id,
            causation_id=idempotency_key,
            actor_ref=actor_ref,
            options=options,
        )

    async def review_attached_subject(
        self,
        *,
        task_id: str,
        subject_type: _OutputType,
        subject_id: str,
        correlation_id: str,
        causation_id: str | None = None,
        actor_ref: str | None = None,
        options: ReviewerRuntimeOptions | None = None,
    ) -> AutomaticOutputReviewResult:
        """Drive configured AGENT stages for an already-attached exact subject.

        Missing Task Verification configuration is a normal no-review case. Once a Task
        requires a policy, however, missing/ambiguous reviewer configuration fails closed
        through the underlying workflow and the Task remains Verification-blocked.
        """

        validate_id(task_id, "task")
        validate_id(subject_id, subject_type)
        if not correlation_id.strip():
            raise ValueError("correlation_id must not be blank")

        requirement = self._completion.requirement_for(task_id)
        if requirement is None:
            return AutomaticOutputReviewResult(
                task=await self._kernel.get_task(task_id),
                subject=None,
                reviews=(),
            )

        policy = self._completion.verification.get_policy(
            requirement.policy_id,
            requirement.policy_version,
        )
        agent_stages = tuple(
            stage for stage in policy.stages if stage.verifier_kind is VerifierKind.AGENT
        )
        if not agent_stages:
            return AutomaticOutputReviewResult(
                task=await self._kernel.get_task(task_id),
                subject=None,
                reviews=(),
            )

        subject = await self._runtime.evidence.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        reviews: list[ReviewWorkflowResult] = []
        for stage in agent_stages:
            existing = self._existing_current_request(
                task_id=task_id,
                policy=policy,
                stage_id=stage.stage_id,
                subject=subject,
            )
            if existing is None:
                review = await self._reviewer.request_and_run(
                    task_id=task_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    stage_id=stage.stage_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    options=options,
                )
            else:
                review = await self._reviewer.run_request(
                    existing.verification_id,
                    options=options,
                )
            reviews.append(review)

        decision = self._completion.assess_task_completion(task_id)
        task = await self._kernel.get_task(task_id)
        if decision.state is CompletionState.ACCEPTED and task.status is not TaskStatus.SUCCEEDED:
            task = await self._kernel.complete_task(
                idempotency_key=(
                    f"automatic-review-complete:{task_id}:{subject.subject_type}:"
                    f"{subject.subject_id}:{subject.digest}"
                ),
                task_id=task_id,
                actor_ref=actor_ref or "service:automatic-reviewer-workflow",
                source=_SOURCE,
            )
        return AutomaticOutputReviewResult(
            task=task,
            subject=subject,
            reviews=tuple(reviews),
        )

    def _existing_current_request(
        self,
        *,
        task_id: str,
        policy: VerificationPolicy,
        stage_id: str,
        subject: VerificationSubject,
    ) -> VerificationRequest | None:
        candidates: list[tuple[VerificationRequest, VerificationResult | None]] = []
        for request, result in self._completion.verification.history(task_id=task_id):
            if (
                request.policy_id == policy.policy_id
                and request.policy_version == policy.version
                and request.stage_id == stage_id
                and request.subject == subject
                and request.status
                in {
                    VerificationRequestStatus.PENDING,
                    VerificationRequestStatus.COMPLETED,
                }
                and self._result_is_current(policy, result)
            ):
                candidates.append((request, result))
        if len(candidates) > 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "exact subject maps to multiple current automatic reviewer requests",
                details={
                    "task_id": task_id,
                    "policy_id": policy.policy_id,
                    "policy_version": policy.version,
                    "stage_id": stage_id,
                    "subject_id": subject.subject_id,
                },
            )
        return None if not candidates else candidates[0][0]

    @staticmethod
    def _result_is_current(
        policy: VerificationPolicy,
        result: VerificationResult | None,
    ) -> bool:
        if result is None or policy.result_expiry_seconds is None:
            return True
        return result.completed_at + timedelta(seconds=policy.result_expiry_seconds) > datetime.now(
            UTC
        )


class AutomaticReviewerOutputObserver(OutputAttachmentObserver):
    """Translate persisted kernel output events into automatic canonical Agent review."""

    def __init__(
        self,
        coordinator: AutomaticReviewerOutputCoordinator,
        *,
        options: ReviewerRuntimeOptions | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._options = options

    async def output_attached(self, event: PlatformEvent) -> None:
        if event.event_type == "result.attached":
            subject_type: _OutputType = "result"
            payload_key = "result_id"
        elif event.event_type == "artifact.attached":
            subject_type = "artifact"
            payload_key = "artifact_id"
        else:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "automatic reviewer observer received a non-output attachment event",
            )

        subject_id = event.payload.get(payload_key)
        if not isinstance(subject_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical output attachment event is missing its output ID",
            )
        actor_ref = event.payload.get("actor_ref")
        await self._coordinator.review_attached_subject(
            task_id=event.correlation_id,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            actor_ref=actor_ref if isinstance(actor_ref, str) else None,
            options=self._options,
        )


def install_automatic_reviewer_output_observer(
    kernel: PlatformKernel,
    coordinator: AutomaticReviewerOutputCoordinator,
    *,
    options: ReviewerRuntimeOptions | None = None,
) -> AutomaticReviewerOutputObserver:
    """Install automatic review on the kernel's provider-neutral post-commit output seam."""

    observer = AutomaticReviewerOutputObserver(coordinator, options=options)
    kernel.configure_output_attachment_observer(observer)
    return observer


__all__ = [
    "AutomaticOutputReviewResult",
    "AutomaticReviewerOutputCoordinator",
    "AutomaticReviewerOutputObserver",
    "install_automatic_reviewer_output_observer",
]
