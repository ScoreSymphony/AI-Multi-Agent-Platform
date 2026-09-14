"""Reference multi-agent composition over existing platform runtime authorities (#889).

The code here deliberately owns no Task/Run, Plan/Step, Handoff, Context or verification state.
It supplies a deterministic reference planner behind #439 and materializes durable #651 Handoffs
from already-canonical predecessor AgentRun outputs immediately before the existing #590 Context
resolver assembles the consuming Run's immutable ContextBundle.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.context import ContextCandidate, ContextSourceRequest
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.coordination.async_repository import runtime_coordinator_repository
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.handoffs import (
    HandoffContent,
    HandoffSourceKind,
    HandoffSourceRef,
)
from ai_multi_agent_platform.handoffs.production import DurableConsumedHandoffContextAdapter
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.planning import DeterministicReferencePlanner
from ai_multi_agent_platform.planning.models import (
    AgentAssignment,
    PlanDraft,
    PlannerOutput,
    PlanningRequest,
    PlanningStepDraft,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .handoff_composition import HandoffDeploymentComposition

REFERENCE_MULTI_AGENT_CONSTRAINT = "runtime:reference-multi-agent"
_REFERENCE_ROLES = frozenset({"researcher", "developer", "reviewer"})


def _enabled_reference_roles(request: PlanningRequest) -> frozenset[str]:
    return frozenset(
        candidate.role
        for candidate in request.inventory.agents
        if candidate.enabled and candidate.role in _REFERENCE_ROLES
    )


def _has_reference_roles(request: PlanningRequest) -> bool:
    """Detect the standard golden-path roles without becoming a selection authority."""

    return _REFERENCE_ROLES.issubset(_enabled_reference_roles(request))


def _reference_multi_agent_requested(request: PlanningRequest) -> bool:
    return REFERENCE_MULTI_AGENT_CONSTRAINT in request.task_constraints


def _agent_actor(agent: AgentRevisionRef) -> ActorIdentity:
    return ActorIdentity(
        f"agent:{agent.agent_id}@{agent.revision}",
        ActorType.AGENT,
    )


class ReferenceMultiAgentPlanner(DeterministicReferencePlanner):
    """Deterministic #439 planner for the built-in multi-agent golden path.

    The planner expresses role requirements only. Proposal construction then delegates actual
    eligibility and exact immutable Agent revision selection to the canonical #903 matcher already
    owned by #439. The ordinary deterministic single-Agent reference path remains available when
    the multi-Agent path was not requested. An explicitly requested multi-Agent path fails closed
    when a required role is unavailable instead of silently selecting a fallback Agent.
    """

    def __init__(self) -> None:
        super().__init__(planner_id="reference-multi-agent-planner")

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        if not _has_reference_roles(request):
            if not _reference_multi_agent_requested(request):
                return await super().propose(request)
            missing_roles = sorted(_REFERENCE_ROLES - _enabled_reference_roles(request))
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "reference multi-agent planning requires every canonical golden-path role",
                details={"missing_roles": ",".join(missing_roles)},
            )

        reused: tuple[str, ...] = ()
        if request.prior_plan is not None:
            reused = request.prior_plan.completed_step_ids

        research_assignment = AgentAssignment(
            role_requirement="researcher",
            rationale=(
                "reference golden path: resolve Research Agent through canonical #903 matcher"
            ),
        )
        execution_assignment = AgentAssignment(
            role_requirement="developer",
            rationale=(
                "reference golden path: resolve Execution Agent through canonical #903 matcher"
            ),
        )
        review_assignment = AgentAssignment(
            role_requirement="reviewer",
            rationale="reference golden path: resolve Review Agent through canonical #903 matcher",
        )
        draft = PlanDraft(
            summary="Reference multi-agent research, execution and review plan",
            steps=(
                PlanningStepDraft(
                    key="research",
                    title="Gather authoritative evidence",
                    objective=(
                        "Research the task objective and produce evidence that downstream work can "
                        "consume through canonical Handoff/Context state. " + request.objective
                    ),
                    assignment=research_assignment,
                    requires_model=True,
                ),
                PlanningStepDraft(
                    key="approach",
                    title="Prepare an independent execution approach",
                    objective=(
                        "Independently analyze the requested outcome and prepare an execution "
                        "approach without waiting for the research branch. " + request.objective
                    ),
                    assignment=execution_assignment,
                    requires_model=True,
                ),
                PlanningStepDraft(
                    key="execute",
                    title="Produce the requested result",
                    objective=(
                        "Produce the task result using the completed research and "
                        "execution-approach Handoffs as canonical Context. " + request.objective
                    ),
                    depends_on=("research", "approach"),
                    assignment=execution_assignment,
                    requires_model=True,
                    reuse_step_ids=reused,
                ),
                PlanningStepDraft(
                    key="review",
                    title="Review the exact produced result",
                    objective=(
                        "Review the exact result revision produced by the execution step and "
                        "report whether it satisfies the task objective. " + request.objective
                    ),
                    depends_on=("execute",),
                    assignment=review_assignment,
                    requires_model=True,
                ),
            ),
            constraints=request.task_constraints,
        )
        return PlannerOutput(draft=draft, planner=self.descriptor)


class ReferenceIncomingHandoffContextAdapter:
    """Materialize dependency Handoffs, bind them to the Run, and project them into #590.

    #384 remains the dependency/Run authority and #651 remains the Handoff authority. The adapter
    only turns already-durable predecessor AgentRun outputs into idempotent Handoffs at the safe
    consumer Context boundary. Coordination reads use #384's awaitable runtime repository seam, so
    SQLite persistence is not performed inline on the Context/Agent event loop. This makes a
    producer -> process restart -> consumer path equivalent to the uninterrupted path without
    reserving a second kernel output-observer slot.

    A context transfer also needs the predecessor output to be a canonical Result/Artifact
    reference. Reference Agent execution records the immutable output identity on the AgentRun and
    kernel Run snapshot first; this adapter publishes that exact identity through the existing
    kernel attachment command before asking #651/#86 to resolve it. The attachment is idempotent and
    remains platform history rather than Handoff-private state.
    """

    adapter_id = "reference-multi-agent-incoming-handoff/v1"

    def __init__(
        self,
        handoffs: HandoffDeploymentComposition,
        *,
        coordinator: CoordinatorRepository,
        kernel: PlatformKernel,
    ) -> None:
        self._handoffs = handoffs
        self._coordinator = runtime_coordinator_repository(coordinator)
        self._durable = DurableConsumedHandoffContextAdapter(
            repository=handoffs.repository,
            agents=handoffs.runtime.agents,
            runtime_repository=handoffs.service.runtime_repository,
        )
        self._kernel = kernel

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.step_id is None:
            return ()
        operation = getattr(request, "operation", None)
        if not isinstance(operation, OperationContext):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "reference Handoff Context adapter requires the operational Context request",
            )

        has_dependencies = await self._ensure_dependency_handoffs(request, operation)
        stored_handoffs = await self._handoffs.service.async_list_handoffs_for_step(request.step_id)
        incoming = tuple(
            handoff
            for handoff in stored_handoffs
            if handoff.content.consumer_step_id == request.step_id
            and handoff.content.task_id == request.task_id
            and (request.plan_id is None or handoff.content.plan_id == request.plan_id)
        )
        if not incoming:
            if has_dependencies:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "dependency Step has no canonical incoming Agent Handoff",
                    details={"step_id": request.step_id, "run_id": request.run_id},
                )
            return ()

        consumer = AgentRevisionRef(request.agent_id, request.agent_revision)
        consumer_actor = _agent_actor(consumer)
        for handoff in sorted(incoming, key=lambda item: (item.handoff_id, item.revision)):
            await self._handoffs.runtime.consume_handoff(
                handoff.handoff_id,
                handoff.revision,
                consuming_run_id=request.run_id,
                consumer=consumer,
                consumer_actor=consumer_actor,
                operation=operation,
            )
        candidates = await self._durable.collect(request)
        if has_dependencies and not candidates:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "consumed dependency Handoffs produced no canonical Context contribution",
                details={"step_id": request.step_id, "run_id": request.run_id},
            )
        return candidates

    async def _ensure_dependency_handoffs(
        self,
        request: ContextSourceRequest,
        operation: OperationContext,
    ) -> bool:
        assert request.step_id is not None
        consumer_record = await self._coordinator.get_step_record(request.step_id)
        if consumer_record.task_id != request.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "consumer Step coordination record belongs to a different Task",
            )
        if request.plan_id is not None and consumer_record.plan_id != request.plan_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "consumer Context Plan does not match canonical coordination state",
            )
        state = await self._coordinator.get_plan(consumer_record.plan_id)
        consumer_step = state.step(request.step_id)
        if not consumer_step.depends_on:
            return False

        consumer = AgentRevisionRef(request.agent_id, request.agent_revision)
        consumer_actor = _agent_actor(consumer)
        for producer_step_id in sorted(consumer_step.depends_on):
            producer_record = await self._coordinator.get_step_record(producer_step_id)
            producer_run_id = producer_record.latest_run_id
            if producer_run_id is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "ready consumer Step has a dependency without a canonical producer Run",
                    details={"producer_step_id": producer_step_id},
                )
            producer_runs = tuple(
                item
                for item in self._handoffs.runtime.agents.list_agent_runs(producer_run_id)
                if item.task_id == request.task_id
                and item.status is AgentRunStatus.SUCCEEDED
                and not item.verification_context.get("verification_id")
            )
            if len(producer_runs) != 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    (
                        "dependency output does not resolve to exactly one successful "
                        "producer AgentRun"
                    ),
                    details={
                        "producer_step_id": producer_step_id,
                        "producer_run_id": producer_run_id,
                        "match_count": len(producer_runs),
                    },
                )
            producer_run = producer_runs[0]
            source_refs = await self._source_refs(request.task_id, producer_run)
            if not source_refs:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "successful dependency AgentRun has no canonical Result/Artifact output",
                    details={"producer_run_id": producer_run_id},
                )
            source_identity = ",".join(
                f"{item.kind.value}:{item.resource_id}@{item.revision}:{item.digest}"
                for item in source_refs
            )
            await self._handoffs.runtime.create_handoff(
                HandoffContent(
                    task_id=request.task_id,
                    plan_id=consumer_record.plan_id,
                    producer_step_id=producer_step_id,
                    consumer_step_id=request.step_id,
                    producer_run_id=producer_run_id,
                    producer=producer_run.agent,
                    intended_consumer=consumer,
                    objective=f"Provide canonical dependency output for {consumer_step.title}.",
                    completed_work_summary=(
                        "Canonical predecessor outputs are ready: "
                        + ", ".join(
                            f"{item.kind.value}:{item.resource_id}@{item.revision}"
                            for item in source_refs
                        )
                    ),
                    recommended_next_action=f"Continue canonical Step {request.step_id}.",
                    requested_output=f"Complete canonical Step {request.step_id}.",
                    source_refs=source_refs,
                ),
                idempotency_key=(
                    "reference-multi-agent-handoff:"
                    f"{producer_step_id}:{request.step_id}:{producer_run_id}:{source_identity}"
                ),
                producer_actor=_agent_actor(producer_run.agent),
                intended_consumer_actor=consumer_actor,
                operation=operation,
            )
        return True

    async def _source_refs(
        self,
        task_id: str,
        producer_run: AgentRunRecord,
    ) -> tuple[HandoffSourceRef, ...]:
        references: list[HandoffSourceRef] = []
        for kind, resource_ids in (
            (HandoffSourceKind.RESULT, producer_run.result_ids),
            (HandoffSourceKind.ARTIFACT, producer_run.artifact_ids),
        ):
            for resource_id in sorted(resource_ids):
                subject = await self._resolve_source_subject(
                    task_id=task_id,
                    producer_run=producer_run,
                    kind=kind,
                    resource_id=resource_id,
                )
                references.append(
                    HandoffSourceRef(
                        kind,
                        resource_id,
                        revision=str(subject.revision),
                        digest=subject.digest.removeprefix("sha256:"),
                    )
                )
        return tuple(references)

    async def _resolve_source_subject(
        self,
        *,
        task_id: str,
        producer_run: AgentRunRecord,
        kind: HandoffSourceKind,
        resource_id: str,
    ) -> Any:
        resolver = self._handoffs.references.verification
        try:
            return await resolver.resolve_subject(
                task_id=task_id,
                subject_type=kind.value,
                subject_id=resource_id,
            )
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise

        await self._publish_kernel_output(
            task_id=task_id,
            producer_run=producer_run,
            kind=kind,
            resource_id=resource_id,
        )
        try:
            return await resolver.resolve_subject(
                task_id=task_id,
                subject_type=kind.value,
                subject_id=resource_id,
            )
        except ContractError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "dependency AgentRun output could not be resolved as canonical Handoff evidence",
                details={
                    "producer_run_id": producer_run.run_id,
                    "source_kind": kind.value,
                    "resource_id": resource_id,
                    "resolution_error": exc.code.value,
                },
            ) from exc

    async def _publish_kernel_output(
        self,
        *,
        task_id: str,
        producer_run: AgentRunRecord,
        kind: HandoffSourceKind,
        resource_id: str,
    ) -> None:
        try:
            run = await self._kernel.get_run(task_id, producer_run.run_id)
        except ContractError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "dependency AgentRun is missing its canonical producer Run",
                details={"producer_run_id": producer_run.run_id},
            ) from exc

        if kind is HandoffSourceKind.RESULT:
            if resource_id in run.result_ids:
                return
            if run.output.get("result_id") != resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "AgentRun Result identity does not match canonical producer Run output",
                    details={"producer_run_id": producer_run.run_id, "result_id": resource_id},
                )
            await self._kernel.attach_result(
                idempotency_key=(
                    f"reference-multi-agent-output:{producer_run.run_id}:result:{resource_id}"
                ),
                task_id=task_id,
                result_id=resource_id,
                run_id=producer_run.run_id,
                source="reference-multi-agent-handoff",
            )
            return

        if resource_id in run.artifact_ids:
            return
        artifact_refs = run.output.get("artifact_refs")
        if not isinstance(artifact_refs, (list, tuple)) or resource_id not in artifact_refs:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "AgentRun Artifact identity does not match canonical producer Run output",
                details={"producer_run_id": producer_run.run_id, "artifact_id": resource_id},
            )
        await self._kernel.attach_artifact(
            idempotency_key=(
                f"reference-multi-agent-output:{producer_run.run_id}:artifact:{resource_id}"
            ),
            task_id=task_id,
            artifact_id=resource_id,
            run_id=producer_run.run_id,
            source="reference-multi-agent-handoff",
        )


__all__ = [
    "REFERENCE_MULTI_AGENT_CONSTRAINT",
    "ReferenceIncomingHandoffContextAdapter",
    "ReferenceMultiAgentPlanner",
]
