"""Productive output-to-review coordination for automatic Agent Verification (#711, #759).

The kernel remains lifecycle authority and canonical Verification remains review authority.
This integration composes those existing seams so a configured kernel output attachment can
drive Agent review without callers manually creating VerificationRequest/runtime plumbing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from ai_multi_agent_platform.agents import AgentRunRecord, AgentRuntime
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, PlatformEvent
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, validate_id
from ai_multi_agent_platform.kernel import OutputAttachmentObserver, PlatformKernel
from ai_multi_agent_platform.kernel.models import TaskState

from .agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ResolvedReviewerAssignment,
    ReviewerAssignment,
    ReviewerAssignmentResolver,
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
from .repair import VERIFICATION_REPAIR_SOURCE
from .reviewer_routing import CapabilityRoleReviewerResolver, ReviewerDiscoverySelector

_OutputType = Literal["result", "artifact"]
_ReviewerRoute = ReviewerAssignment | ReviewerDiscoverySelector
_SOURCE = "automatic-reviewer-output-workflow"
_AUTOMATIC_REVIEW_METADATA_KEY = "automatic_reviewer"
_ALLOWED_OUTPUT_TYPES = frozenset({"result", "artifact"})
_ACTIVE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING})
_EXPLICIT_ROUTE_FIELDS = frozenset(
    {"agent_id", "agent_revision", "team_id", "team_revision", "team_role"}
)
_DISCOVERY_ROUTE_FIELDS = frozenset(
    {
        "candidate_agent_ids",
        "candidate_team_ids",
        "reviewer_role",
        "required_capability_ids",
    }
)


@dataclass(frozen=True, slots=True)
class _AutomaticReviewConfiguration:
    subject_types: frozenset[str]
    routes: Mapping[str, _ReviewerRoute]


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


class PolicyMetadataReviewerResolver(ReviewerAssignmentResolver):
    """Resolve an exact reviewer revision from versioned VerificationPolicy metadata.

    Automatic review is explicitly opt-in. A policy stage may either pin one exact Agent/Team
    assignment or request bounded role/capability discovery inside an explicit canonical candidate
    scope. Discovery never scans arbitrary global Agents, and the bundled Reviewer is never a
    hidden fallback.
    """

    def __init__(self, completion: VerificationCompletionAuthority) -> None:
        self._completion = completion

    def resolve(
        self,
        request: VerificationRequest,
        agents: AgentRuntime,
    ) -> ResolvedReviewerAssignment:
        policy = self._completion.verification.get_policy(
            request.policy_id,
            request.policy_version,
        )
        configuration = _automatic_review_configuration(policy, required=True)
        assert configuration is not None
        try:
            route = configuration.routes[request.stage_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer policy is missing a reviewer route for stage",
                details={
                    "policy_id": policy.policy_id,
                    "policy_version": policy.version,
                    "stage_id": request.stage_id,
                },
            ) from exc

        key = (request.policy_id, request.policy_version, request.stage_id)
        if isinstance(route, ReviewerDiscoverySelector):
            return CapabilityRoleReviewerResolver({key: route}).resolve(request, agents)
        return ConfiguredReviewerResolver({key: route}).resolve(request, agents)


class AutomaticReviewerOutputCoordinator:
    """Drive configured Agent-verifier stages for canonical attached output.

    A Task without a Verification requirement, or a policy without automatic-review metadata,
    is a normal no-review case. Once automatic review is enabled for an output type, missing or
    ambiguous reviewer configuration fails closed and canonical Verification remains authoritative.
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
        """Legacy explicit helper; configured kernels normally invoke the observer automatically."""

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
        """Legacy explicit helper; configured kernels normally invoke the observer automatically."""

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
        """Drive configured AGENT stages for an already-attached exact subject."""

        validate_id(task_id, "task")
        validate_id(subject_id, subject_type)
        if not correlation_id.strip():
            raise ValueError("correlation_id must not be blank")

        requirement = self._completion.requirement_for(task_id)
        if requirement is None:
            return await self._no_review(task_id)

        policy = self._completion.verification.get_policy(
            requirement.policy_id,
            requirement.policy_version,
        )
        configuration = _automatic_review_configuration(policy)
        if configuration is None or subject_type not in configuration.subject_types:
            return await self._no_review(task_id)

        agent_stages = tuple(
            stage for stage in policy.stages if stage.verifier_kind is VerifierKind.AGENT
        )
        if not agent_stages:
            return await self._no_review(task_id)

        missing_routes = tuple(
            stage.stage_id
            for stage in agent_stages
            if stage.stage_id not in configuration.routes
        )
        if missing_routes:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer policy is missing reviewer routes",
                details={
                    "policy_id": policy.policy_id,
                    "policy_version": policy.version,
                    "stage_ids": list(missing_routes),
                },
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
        # Artifact output may be attached while a producer Run is still active. Likewise, a repair
        # temporarily resumes a verification-blocked Task to RUNNING before its Step finishes. Only
        # release accepted completion once every canonical Run is terminal.
        if (
            decision.state is CompletionState.ACCEPTED
            and task.status in {TaskStatus.WAITING, TaskStatus.RUNNING}
            and not await self._has_active_runs(task)
        ):
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

    async def _has_active_runs(self, task: TaskState) -> bool:
        for run_id in task.run_ids:
            run = await self._kernel.get_run(task.task_id, run_id)
            if run.status in _ACTIVE_RUN_STATUSES:
                return True
        return False

    async def _no_review(self, task_id: str) -> AutomaticOutputReviewResult:
        return AutomaticOutputReviewResult(
            task=await self._kernel.get_task(task_id),
            subject=None,
            reviews=(),
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
        # Automatic repair attaches the new canonical output before the workflow creates its
        # lineage-preserving reverification request. Re-entering the general observer here would
        # create a second unrelated Verification with repair_attempt=0 and could release the Task
        # against the wrong lineage. The repair workflow therefore owns this one internal event.
        if event.provenance is not None and event.provenance.source == VERIFICATION_REPAIR_SOURCE:
            return

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


def _automatic_review_configuration(
    policy: VerificationPolicy,
    *,
    required: bool = False,
) -> _AutomaticReviewConfiguration | None:
    raw = policy.metadata.get(_AUTOMATIC_REVIEW_METADATA_KEY)
    if raw is None:
        if required:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "verification policy has no automatic reviewer configuration",
                details={"policy_id": policy.policy_id, "policy_version": policy.version},
            )
        return None
    if not isinstance(raw, Mapping):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "automatic reviewer policy metadata must be an object",
        )
    unknown = set(raw) - {"enabled", "subject_types", "stages"}
    if unknown:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "automatic reviewer policy metadata contains unknown fields",
            details={"fields": cast(JsonValue, sorted(unknown))},
        )
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "automatic reviewer enabled flag must be boolean",
        )
    if not enabled:
        if required:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer policy is disabled",
            )
        return None

    subject_types_raw = raw.get("subject_types")
    if (
        not isinstance(subject_types_raw, (list, tuple))
        or not subject_types_raw
        or any(not isinstance(item, str) for item in subject_types_raw)
    ):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "automatic reviewer subject_types must be a non-empty string list",
        )
    subject_types = frozenset(item for item in subject_types_raw if isinstance(item, str))
    if not subject_types.issubset(_ALLOWED_OUTPUT_TYPES):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "automatic reviewer subject_types may contain only result/artifact",
        )

    stages_raw = raw.get("stages")
    if not isinstance(stages_raw, Mapping) or not stages_raw:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "automatic reviewer stages must be a non-empty object",
        )
    routes: dict[str, _ReviewerRoute] = {}
    allowed_stage_fields = _EXPLICIT_ROUTE_FIELDS | _DISCOVERY_ROUTE_FIELDS
    for stage_id, stage_raw in stages_raw.items():
        if not isinstance(stage_id, str) or not stage_id.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer stage IDs must be non-blank strings",
            )
        if not isinstance(stage_raw, Mapping):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer stage configuration must be an object",
            )
        unknown_stage = set(stage_raw) - allowed_stage_fields
        if unknown_stage:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer stage contains unknown fields",
                details={
                    "stage_id": stage_id,
                    "fields": cast(JsonValue, sorted(unknown_stage)),
                },
            )

        has_explicit = bool(set(stage_raw).intersection(_EXPLICIT_ROUTE_FIELDS))
        has_discovery = bool(set(stage_raw).intersection(_DISCOVERY_ROUTE_FIELDS))
        if has_explicit and has_discovery:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer stage cannot mix exact assignment and discovery fields",
                details={"stage_id": stage_id},
            )
        if not has_explicit and not has_discovery:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automatic reviewer stage must configure an exact assignment or scoped discovery",
                details={"stage_id": stage_id},
            )

        try:
            if has_discovery:
                routes[stage_id] = ReviewerDiscoverySelector(
                    candidate_agent_ids=_optional_string_tuple(
                        stage_raw, "candidate_agent_ids"
                    ),
                    candidate_team_ids=_optional_string_tuple(
                        stage_raw, "candidate_team_ids"
                    ),
                    reviewer_role=_optional_string(stage_raw, "reviewer_role"),
                    required_capability_ids=_optional_string_tuple(
                        stage_raw, "required_capability_ids"
                    ),
                )
            else:
                routes[stage_id] = ReviewerAssignment(
                    agent_id=_optional_string(stage_raw, "agent_id"),
                    agent_revision=_optional_positive_int(stage_raw, "agent_revision"),
                    team_id=_optional_string(stage_raw, "team_id"),
                    team_revision=_optional_positive_int(stage_raw, "team_revision"),
                    team_role=_optional_string(stage_raw, "team_role"),
                )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid automatic reviewer route for stage {stage_id}: {exc}",
            ) from exc
    return _AutomaticReviewConfiguration(
        subject_types=subject_types,
        routes=routes,
    )


def _optional_string(value: Mapping[str, JsonValue], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"automatic reviewer {key} must be a non-blank string",
        )
    return item


def _optional_string_tuple(value: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    item = value.get(key)
    if item is None:
        return ()
    if not isinstance(item, (list, tuple)) or any(
        not isinstance(entry, str) or not entry.strip() for entry in item
    ):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"automatic reviewer {key} must be a string list",
        )
    return tuple(entry for entry in item if isinstance(entry, str))


def _optional_positive_int(value: Mapping[str, JsonValue], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"automatic reviewer {key} must be an integer >= 1",
        )
    return item


__all__ = [
    "AutomaticOutputReviewResult",
    "AutomaticReviewerOutputCoordinator",
    "AutomaticReviewerOutputObserver",
    "PolicyMetadataReviewerResolver",
    "install_automatic_reviewer_output_observer",
]
