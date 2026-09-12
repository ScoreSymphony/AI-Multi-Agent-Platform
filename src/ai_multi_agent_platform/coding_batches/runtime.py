"""Productive #384 -> #33 -> #37/#82 composition for coding workstreams.

The coding-batch layer never creates Step attempts or AgentRun identity itself. #384 remains the
source of truth for the active canonical Step/Run attempt, #33 creates or reuses the exact AgentRun,
and #37/#82 materialize the isolated Workspace and repository branch from that evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.agents import AgentRunRecord
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.coordination import CoordinationPhase, PlanCoordinationProjection
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef, StepStatus
from ai_multi_agent_platform.repositories import RepositoryCallContext

from .models import CodingBatch, CodingWorkstream, WorkstreamState
from .service import CodingBatchCoordinator


class PlanCoordinationReader(Protocol):
    """Narrow #384 read boundary used by coding dispatch."""

    def projection(self, plan_id: str) -> PlanCoordinationProjection: ...


class CodingAgentRuntime(Protocol):
    """Narrow #33 AgentRuntime start boundary used by coding dispatch."""

    async def start_agent(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        revision: int | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
        verification_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentRunRecord: ...


class AgentRunReader(Protocol):
    """Source-owned #33 AgentRun read boundary for restart reconciliation."""

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]: ...


class WorkstreamMaterializer(Protocol):
    """Narrow #37/#82 materialization boundary used after AgentRun identity is pinned."""

    async def ensure_materialized(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        data_context: DataAccessContext,
        repository_context: RepositoryCallContext,
        agent_revision: str,
        agent_run_id: str,
    ) -> CodingWorkstream: ...


@dataclass(frozen=True, slots=True)
class CodingDispatchSlot:
    """One #384-authorized active Step attempt eligible for coding execution."""

    batch_id: str
    workstream_id: str
    task_id: str
    plan_id: str
    plan_revision: int
    step_id: str
    run_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class CodingWorkstreamDispatch:
    """Result of binding one coding workstream to canonical runtime evidence."""

    slot: CodingDispatchSlot
    agent_run: AgentRunRecord
    workstream: CodingWorkstream


def agent_revision_ref(record: AgentRunRecord) -> str:
    """Stable display/provenance reference derived from canonical #33 Agent revision identity."""

    return f"{record.agent.agent_id}@{record.agent.revision}"


class CanonicalCodingWorkstreamDispatcher:
    """Dispatch coding work only when canonical coordination and local safety both permit it.

    #384 creates/starts the canonical Step Run. The batch layer may further withhold Agent
    execution for conservative overlap or batch-concurrency reasons, but it never creates another
    Run, attempt counter or retry schedule. A restart reuses the #33 AgentRun already bound to that
    canonical Run rather than allocating a duplicate.
    """

    def __init__(
        self,
        coordinator: CodingBatchCoordinator,
        *,
        plan_coordination: PlanCoordinationReader,
        agent_runtime: CodingAgentRuntime,
        agent_runs: AgentRunReader,
        materializer: WorkstreamMaterializer,
    ) -> None:
        self._coordinator = coordinator
        self._plan_coordination = plan_coordination
        self._agent_runtime = agent_runtime
        self._agent_runs = agent_runs
        self._materializer = materializer

    def dispatchable_workstreams(self, batch_id: str) -> tuple[CodingDispatchSlot, ...]:
        """Intersect #872 safety readiness with already-active canonical #384 Step attempts."""

        batch = self._coordinator.get(batch_id)
        locally_ready = {workstream.id for workstream in self._coordinator.ready_workstreams(batch_id)}
        slots: list[CodingDispatchSlot] = []
        for workstream in batch.workstreams:
            if workstream.id not in locally_ready:
                continue
            slot = self._active_slot(batch, workstream)
            if slot is not None:
                slots.append(slot)
        return tuple(slots)

    async def ensure_dispatched(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        agent_id: str,
        project_id: str,
        owner_ref: OwnerRef,
        data_context: DataAccessContext,
        repository_context: RepositoryCallContext,
        agent_revision: int | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
    ) -> CodingWorkstreamDispatch:
        """Bind one eligible workstream to #384 Run, #33 AgentRun and #37/#82 isolation."""

        batch = self._coordinator.get(batch_id)
        workstream = batch.workstream(workstream_id)
        slot = self._active_slot(batch, workstream)
        if slot is None:
            raise ValueError("coding workstream has no active canonical #384 Step attempt")

        if workstream.state is WorkstreamState.READY:
            ready_ids = {item.id for item in self._coordinator.ready_workstreams(batch_id)}
            if workstream_id not in ready_ids:
                raise ValueError("coding workstream is not admitted by batch safety/concurrency gates")
        elif workstream.state not in {WorkstreamState.MATERIALIZED, WorkstreamState.RUNNING}:
            raise ValueError("coding workstream is not eligible for dispatch/reconciliation")

        context = self._verification_context(batch, workstream, slot)
        run = await self._ensure_agent_run(
            slot,
            agent_id=agent_id,
            revision=agent_revision,
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=task_context,
            project_context=project_context,
            verification_context=context,
        )
        if workstream.provenance.agent_run_id is not None:
            if workstream.provenance.agent_run_id != run.agent_run_id:
                raise ValueError("workstream is already bound to a different canonical AgentRun")
            recorded_revision = workstream.provenance.agent_revision
            if recorded_revision != agent_revision_ref(run):
                raise ValueError("workstream Agent revision provenance conflicts with canonical #33")

        materialized = await self._materializer.ensure_materialized(
            batch_id,
            workstream_id,
            project_id=project_id,
            owner_ref=owner_ref,
            data_context=data_context,
            repository_context=repository_context,
            agent_revision=agent_revision_ref(run),
            agent_run_id=run.agent_run_id,
        )
        running = self._coordinator.start_workstream(batch_id, workstream_id)
        return CodingWorkstreamDispatch(slot=slot, agent_run=run, workstream=running)

    def _active_slot(
        self,
        batch: CodingBatch,
        workstream: CodingWorkstream,
    ) -> CodingDispatchSlot | None:
        projection = self._plan_coordination.projection(workstream.work_item.plan_id)
        if projection.plan_id != workstream.work_item.plan_id:
            raise ValueError("#384 projection plan identity does not match coding workstream")
        if projection.task_id != workstream.work_item.task_id:
            raise ValueError("#384 projection task identity does not match coding workstream")

        selected_by_step = {item.work_item.step_id: item.id for item in batch.workstreams}
        expected_dependencies = set(workstream.work_item.dependencies)
        step = next(
            (item for item in projection.steps if item.step_id == workstream.work_item.step_id),
            None,
        )
        if step is None:
            raise ValueError("coding workstream Step is absent from canonical #384 projection")
        canonical_selected_dependencies = {
            selected_by_step[dependency_id]
            for dependency_id in step.dependency_ids
            if dependency_id in selected_by_step
        }
        if canonical_selected_dependencies != expected_dependencies:
            raise ValueError(
                "coding work-item dependencies diverge from selected canonical #384 Step dependencies"
            )
        if (
            step.phase is not CoordinationPhase.ATTEMPT_ACTIVE
            or step.status is not StepStatus.RUNNING
            or step.latest_run_id is None
        ):
            return None
        return CodingDispatchSlot(
            batch_id=batch.batch_id,
            workstream_id=workstream.id,
            task_id=projection.task_id,
            plan_id=projection.plan_id,
            plan_revision=projection.plan_revision,
            step_id=step.step_id,
            run_id=step.latest_run_id,
            attempt=step.current_attempt,
        )

    async def _ensure_agent_run(
        self,
        slot: CodingDispatchSlot,
        *,
        agent_id: str,
        revision: int | None,
        requested_capability_ids: tuple[str, ...],
        available_capability_ids: frozenset[str],
        granted_permissions: frozenset[str],
        available_worker_capabilities: frozenset[str],
        task_context: Mapping[str, JsonValue] | None,
        project_context: Mapping[str, JsonValue] | None,
        verification_context: Mapping[str, JsonValue],
    ) -> AgentRunRecord:
        existing = self._agent_runs.list_agent_runs(slot.run_id)
        if existing:
            if len(existing) != 1:
                raise ValueError("coding Step Run must bind exactly one Developer AgentRun")
            record = existing[0]
            self._validate_agent_run(
                record,
                slot=slot,
                agent_id=agent_id,
                revision=revision,
                verification_context=verification_context,
            )
            return record

        record = await self._agent_runtime.start_agent(
            task_id=slot.task_id,
            run_id=slot.run_id,
            agent_id=agent_id,
            revision=revision,
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=task_context,
            project_context=project_context,
            verification_context=verification_context,
        )
        self._validate_agent_run(
            record,
            slot=slot,
            agent_id=agent_id,
            revision=revision,
            verification_context=verification_context,
        )
        return record

    @staticmethod
    def _verification_context(
        batch: CodingBatch,
        workstream: CodingWorkstream,
        slot: CodingDispatchSlot,
    ) -> dict[str, JsonValue]:
        return {
            "coding_batch_id": batch.batch_id,
            "workstream_id": workstream.id,
            "plan_id": slot.plan_id,
            "plan_revision": slot.plan_revision,
            "step_id": slot.step_id,
            "repository_id": batch.repository_id,
            "base_revision": batch.base_revision,
        }

    @staticmethod
    def _validate_agent_run(
        record: AgentRunRecord,
        *,
        slot: CodingDispatchSlot,
        agent_id: str,
        revision: int | None,
        verification_context: Mapping[str, JsonValue],
    ) -> None:
        if record.run_id != slot.run_id or record.task_id != slot.task_id:
            raise ValueError("canonical AgentRun is bound to another Task/Run")
        if record.agent.agent_id != agent_id:
            raise ValueError("canonical Run is already bound to another Developer Agent")
        if revision is not None and record.agent.revision != revision:
            raise ValueError("canonical Run is already bound to another Agent revision")
        if record.team is not None:
            raise ValueError("single-Agent coding dispatch cannot reuse an AgentTeam AgentRun")
        for key, expected in verification_context.items():
            if record.verification_context.get(key) != expected:
                raise ValueError(
                    "canonical AgentRun is missing exact coding-batch/Plan/Step provenance"
                )
