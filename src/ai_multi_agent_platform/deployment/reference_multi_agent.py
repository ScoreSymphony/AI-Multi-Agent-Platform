"""Reference multi-agent composition over existing platform runtime authorities (#889).

The code here deliberately owns no Task/Run, Plan/Step, Handoff, Context or verification state.
It supplies a deterministic reference planner behind #439, projects canonical predecessor outputs
into #651 Handoffs after kernel persistence, and binds incoming Handoffs to the exact consuming Run
before the existing #590 Context resolver assembles that Run's immutable ContextBundle.
"""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.agents.execution_profile import decode_agent_step_execution_binding
from ai_multi_agent_platform.context import ContextCandidate, ContextSourceRequest
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    OperationContext,
    OutputAttachmentObserver,
    PlatformEvent,
)
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

_REFERENCE_ROLES = frozenset({"researcher", "developer", "reviewer"})


def _has_reference_roles(request: PlanningRequest) -> bool:
    """Detect the standard golden-path roles without becoming a selection authority."""

    enabled_roles = {candidate.role for candidate in request.inventory.agents if candidate.enabled}
    return _REFERENCE_ROLES.issubset(enabled_roles)


class ReferenceMultiAgentPlanner(DeterministicReferencePlanner):
    """Deterministic #439 planner for the built-in multi-agent golden path.

    The planner expresses role requirements only. Proposal construction then delegates actual
    eligibility and exact immutable Agent revision selection to the canonical #903 matcher already
    owned by #439. If the standard role set is unavailable, the ordinary single-Agent deterministic
    reference plan remains unchanged.
    """

    def __init__(self) -> None:
        super().__init__(planner_id="reference-multi-agent-planner")

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        if not _has_reference_roles(request):
            return await super().propose(request)

        reused = ()
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


class ReferenceMultiAgentOutputObserver:
    """Project persisted Step outputs into canonical, idempotent dependency Handoffs.

    The kernel remains the Result/Artifact authority, #384 remains the dependency authority and
    #651 remains the Handoff authority. This observer only joins those already-durable identities.
    It activates exclusively for canonical Step Runs carrying exact #439 Agent bindings.
    """

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        coordinator: CoordinatorRepository,
        handoffs: HandoffDeploymentComposition,
        downstream: OutputAttachmentObserver | None = None,
    ) -> None:
        self._kernel = kernel
        self._coordinator = coordinator
        self._handoffs = handoffs
        self._downstream = downstream

    async def output_attached(self, event: PlatformEvent) -> None:
        await self._create_dependency_handoffs(event)
        if self._downstream is not None:
            await self._downstream.output_attached(event)

    async def _create_dependency_handoffs(self, event: PlatformEvent) -> None:
        if event.subject_type != "run":
            return
        run = await self._kernel.get_run(event.correlation_id, event.subject_id)
        if run.run.subject_type != "step":
            return

        task = await self._kernel.get_task(event.correlation_id)
        producer_step_id = run.run.subject_id
        producer_binding = decode_agent_step_execution_binding(task.task.metadata, producer_step_id)
        if producer_binding is None:
            return
        if producer_binding.agent_revision is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planned Agent Step output is missing its exact Agent revision binding",
            )

        try:
            record = self._coordinator.get_step_record(producer_step_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        if record.task_id != task.task_id or record.latest_run_id != run.run_id:
            return
        plan = self._coordinator.get_plan(record.plan_id)
        consumers = tuple(
            step for step in plan.steps if producer_step_id in step.depends_on
        )
        if not consumers:
            return

        producer = AgentRevisionRef(
            producer_binding.agent_id,
            producer_binding.agent_revision,
        )
        producer_runs = tuple(
            item
            for item in self._handoffs.runtime.agents.list_agent_runs(run.run_id)
            if item.task_id == task.task_id and item.agent == producer
        )
        if len(producer_runs) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planned Step output does not resolve to exactly one bound AgentRun",
                details={"run_id": run.run_id, "match_count": len(producer_runs)},
            )

        source_ref = await self._source_reference(event, task.task_id)
        operation = OperationContext(
            correlation_id=task.task_id,
            causation_id=event.id,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
        )
        producer_actor = ActorIdentity(producer.agent_id, ActorType.AGENT)

        for consumer_step in sorted(consumers, key=lambda item: item.id):
            consumer_binding = decode_agent_step_execution_binding(
                task.task.metadata,
                consumer_step.id,
            )
            if consumer_binding is None:
                continue
            if consumer_binding.agent_revision is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "dependent planned Agent Step is missing its exact Agent revision binding",
                    details={"step_id": consumer_step.id},
                )
            consumer = AgentRevisionRef(
                consumer_binding.agent_id,
                consumer_binding.agent_revision,
            )
            output_label = f"{source_ref.kind.value}:{source_ref.resource_id}@{source_ref.revision}"
            idempotency_key = (
                "reference-multi-agent-handoff:"
                f"{producer_step_id}:{consumer_step.id}:{output_label}"
            )
            await self._handoffs.runtime.create_handoff(
                HandoffContent(
                    task_id=task.task_id,
                    plan_id=plan.plan.id,
                    producer_step_id=producer_step_id,
                    consumer_step_id=consumer_step.id,
                    producer_run_id=run.run_id,
                    producer=producer,
                    intended_consumer=consumer,
                    objective=consumer_binding.objective or consumer_step.title,
                    completed_work_summary=(
                        f"Canonical predecessor output {output_label} is ready for consumption."
                    ),
                    recommended_next_action=(
                        consumer_binding.objective or f"Continue Step {consumer_step.id}."
                    ),
                    requested_output=f"Complete canonical Step {consumer_step.id}.",
                    source_refs=(source_ref,),
                ),
                idempotency_key=idempotency_key,
                producer_actor=producer_actor,
                intended_consumer_actor=ActorIdentity(consumer.agent_id, ActorType.AGENT),
                operation=operation,
            )

    async def _source_reference(self, event: PlatformEvent, task_id: str) -> HandoffSourceRef:
        if event.event_type == "result.attached":
            source_kind = HandoffSourceKind.RESULT
            payload_key = "result_id"
        elif event.event_type == "artifact.attached":
            source_kind = HandoffSourceKind.ARTIFACT
            payload_key = "artifact_id"
        else:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reference output observer received an unsupported output attachment event",
                details={"event_type": event.event_type},
            )
        source_id = event.payload.get(payload_key)
        if not isinstance(source_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical output attachment event is missing its source identity",
                details={"event_type": event.event_type},
            )
        subject = await self._handoffs.references.verification.resolve_subject(
            task_id=task_id,
            subject_type=source_kind.value,
            subject_id=source_id,
        )
        return HandoffSourceRef(
            source_kind,
            source_id,
            revision=subject.revision,
            digest=subject.digest.removeprefix("sha256:"),
        )


class ReferenceIncomingHandoffContextAdapter:
    """Bind all incoming Handoffs, then project them through the existing durable #590 adapter.

    Consumption remains owned by ``ProductionHandoffRuntime``. This adapter only performs that
    owner operation at the one safe boundary where #384 has already created the consumer Run and
    #590 has not yet assembled its ContextBundle. Replaying collection is idempotent because the
    canonical Handoff repository owns the durable consumption binding.
    """

    adapter_id = "reference-multi-agent-incoming-handoff/v1"

    def __init__(self, handoffs: HandoffDeploymentComposition) -> None:
        self._handoffs = handoffs
        self._durable = DurableConsumedHandoffContextAdapter(
            repository=handoffs.repository,
            agents=handoffs.runtime.agents,
        )

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.step_id is None:
            return ()
        operation = getattr(request, "operation", None)
        if not isinstance(operation, OperationContext):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "reference Handoff Context adapter requires the operational Context request",
            )

        incoming = tuple(
            handoff
            for handoff in self._handoffs.service.list_handoffs_for_step(request.step_id)
            if handoff.content.consumer_step_id == request.step_id
            and handoff.content.task_id == request.task_id
            and (request.plan_id is None or handoff.content.plan_id == request.plan_id)
        )
        if not incoming:
            return ()

        consumer = AgentRevisionRef(request.agent_id, request.agent_revision)
        consumer_actor = ActorIdentity(request.agent_id, ActorType.AGENT)
        for handoff in sorted(incoming, key=lambda item: (item.handoff_id, item.revision)):
            await self._handoffs.runtime.consume_handoff(
                handoff.handoff_id,
                handoff.revision,
                consuming_run_id=request.run_id,
                consumer=consumer,
                consumer_actor=consumer_actor,
                operation=operation,
            )
        return await self._durable.collect(request)


__all__ = [
    "ReferenceIncomingHandoffContextAdapter",
    "ReferenceMultiAgentOutputObserver",
    "ReferenceMultiAgentPlanner",
]
