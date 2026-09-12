"""Canonical #384/#33/#37/#82 execution path for clean #872 integrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.agents import AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.coordination import CoordinationPhase, PlanCoordinationProjection
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef, StepStatus
from ai_multi_agent_platform.repositories import (
    RepositoryCallContext,
    RepositoryRunProvenance,
    RepositoryService,
)
from ai_multi_agent_platform.workspaces import (
    WorkspaceAccessMode,
    WorkspaceProvider,
    WorkspaceRetention,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)

from .models import (
    CodingBatch,
    IntegrationCandidate,
    IntegrationExecutionProvenance,
    IntegrationState,
)
from .runtime import CodingAgentRuntime, AgentRunReader, agent_revision_ref
from .secured import CodingBatchCoordinator
from .telemetry import CodingBatchTelemetry


class IntegrationPlanCoordinationReader(Protocol):
    """Narrow #384 projection boundary for the canonical integration Step."""

    def projection(self, plan_id: str) -> PlanCoordinationProjection: ...


class IntegrationRepositoryEvidenceReader(Protocol):
    """Read exact #82 Run provenance after integration execution completes."""

    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None: ...


@dataclass(frozen=True, slots=True)
class IntegrationDispatchSlot:
    """One active canonical #384 Step attempt assigned to a clean integration candidate."""

    task_id: str
    plan_id: str
    plan_revision: int
    step_id: str
    run_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class CodingIntegrationDispatch:
    """Canonical evidence bound before the integration Agent mutates its isolated workspace."""

    slot: IntegrationDispatchSlot
    agent_run: AgentRunRecord
    candidate: IntegrationCandidate


def deterministic_integration_workspace_id(batch_id: str, integration_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:{batch_id}:integration:{integration_id}",
    )
    return f"workspace_{value}"


def deterministic_integration_branch_ref(batch_id: str, integration_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:{batch_id}:integration-branch:{integration_id}",
    )
    return f"coding/integration-{value.hex[:16]}"


class CanonicalCodingIntegrationDispatcher:
    """Run clean integration as ordinary canonical Agent work, never as a naked SHA mutation.

    The accepted workstream revisions are passed as immutable integration inputs. #384 owns the
    active Step attempt, #33 owns the AgentRun, #37 owns the isolated Workspace/Snapshot and #82
    owns repository refs plus the eventual output revision/provenance. #872 stores only the links.
    """

    def __init__(
        self,
        coordinator: CodingBatchCoordinator,
        *,
        plan_coordination: IntegrationPlanCoordinationReader,
        agent_runtime: CodingAgentRuntime,
        agent_runs: AgentRunReader,
        workspaces: WorkspaceProvider,
        repositories: RepositoryService,
        repository_provenance: IntegrationRepositoryEvidenceReader,
        telemetry: CodingBatchTelemetry | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._plan_coordination = plan_coordination
        self._agent_runtime = agent_runtime
        self._agent_runs = agent_runs
        self._workspaces = workspaces
        self._repositories = repositories
        self._repository_provenance = repository_provenance
        self._telemetry = telemetry

    async def ensure_dispatched(
        self,
        batch_id: str,
        integration_id: str,
        *,
        plan_id: str,
        step_id: str,
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
    ) -> CodingIntegrationDispatch:
        batch = self._coordinator.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.state not in {IntegrationState.READY, IntegrationState.INTEGRATING}:
            raise ValueError("integration candidate is not eligible for canonical execution")
        if candidate.stale_base or candidate.conflicts:
            raise ValueError("blocked/stale integration candidate cannot be dispatched")

        slot = self._active_slot(plan_id=plan_id, step_id=step_id)
        context = self._verification_context(batch, candidate, slot)
        agent_run = await self._ensure_agent_run(
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

        if candidate.execution is not None:
            self._validate_recorded_execution(candidate, slot, agent_run)
            return CodingIntegrationDispatch(
                slot=slot,
                agent_run=agent_run,
                candidate=candidate,
            )

        workspace_id, snapshot_id, branch_ref = await self._ensure_materialized(
            batch,
            candidate,
            project_id=project_id,
            owner_ref=owner_ref,
            data_context=data_context,
            repository_context=repository_context,
        )
        execution = IntegrationExecutionProvenance(
            task_id=slot.task_id,
            plan_id=slot.plan_id,
            plan_revision=slot.plan_revision,
            step_id=slot.step_id,
            run_id=slot.run_id,
            agent_revision=agent_revision_ref(agent_run),
            agent_run_id=agent_run.agent_run_id,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            branch_ref=branch_ref,
        )
        bound = self._coordinator.bind_integration_execution(
            batch_id,
            integration_id,
            execution,
        )
        if self._telemetry is not None:
            self._telemetry.integration_state(
                self._coordinator.get(batch_id),
                bound,
                event_name="coding_batch.integration.dispatched",
            )
        return CodingIntegrationDispatch(slot=slot, agent_run=agent_run, candidate=bound)

    def reconcile_repository_output(
        self,
        batch_id: str,
        integration_id: str,
    ) -> IntegrationCandidate:
        """Advance to validation only from exact completed #33/#82 integration evidence."""

        batch = self._coordinator.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.state is IntegrationState.VALIDATING and candidate.integrated_revision:
            return candidate
        if candidate.state is not IntegrationState.INTEGRATING or candidate.execution is None:
            raise ValueError("integration output reconciliation requires active execution provenance")

        execution = candidate.execution
        agent_runs = self._agent_runs.list_agent_runs(execution.run_id)
        exact = tuple(run for run in agent_runs if run.agent_run_id == execution.agent_run_id)
        if len(exact) != 1:
            raise ValueError("integration execution must resolve one exact canonical AgentRun")
        agent_run = exact[0]
        if agent_run.status is not AgentRunStatus.SUCCEEDED:
            raise ValueError("integration AgentRun must succeed before repository output is accepted")
        if agent_revision_ref(agent_run) != execution.agent_revision:
            raise ValueError("integration Agent revision differs from bound execution provenance")

        repository = self._repository_provenance.get(execution.run_id, batch.repository_id)
        if repository is None:
            raise ValueError("canonical #82 integration Run provenance is missing")
        if repository.task_id != execution.task_id:
            raise ValueError("integration repository provenance belongs to another Task")
        if repository.input_revision != candidate.target_base_revision:
            raise ValueError("integration repository input differs from candidate target base")
        if repository.output_revision is None:
            raise ValueError("canonical #82 integration Run has no output revision")
        if not repository.diff_artifact_ids:
            raise ValueError("canonical #82 integration Run has no diff-artifact evidence")
        if repository.agent_id is not None and repository.agent_id != agent_run.agent.agent_id:
            raise ValueError("integration repository provenance names another Agent")

        updated = self._coordinator.record_integrated_revision(
            batch_id,
            integration_id,
            integrated_revision=repository.output_revision,
        )
        if self._telemetry is not None:
            self._telemetry.integration_state(
                self._coordinator.get(batch_id),
                updated,
                event_name="coding_batch.integration.output_recorded",
            )
        return updated

    def _active_slot(self, *, plan_id: str, step_id: str) -> IntegrationDispatchSlot:
        projection = self._plan_coordination.projection(plan_id)
        step = next((item for item in projection.steps if item.step_id == step_id), None)
        if step is None:
            raise ValueError("integration Step is absent from canonical #384 projection")
        if (
            step.phase is not CoordinationPhase.ATTEMPT_ACTIVE
            or step.status is not StepStatus.RUNNING
            or step.latest_run_id is None
        ):
            raise ValueError("integration Step has no active canonical #384 attempt")
        return IntegrationDispatchSlot(
            task_id=projection.task_id,
            plan_id=projection.plan_id,
            plan_revision=projection.plan_revision,
            step_id=step.step_id,
            run_id=step.latest_run_id,
            attempt=step.current_attempt,
        )

    async def _ensure_agent_run(
        self,
        slot: IntegrationDispatchSlot,
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
                raise ValueError("integration Step Run must bind exactly one Integration AgentRun")
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

    async def _ensure_materialized(
        self,
        batch: CodingBatch,
        candidate: IntegrationCandidate,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        data_context: DataAccessContext,
        repository_context: RepositoryCallContext,
    ) -> tuple[str, str, str]:
        workspace_id = deterministic_integration_workspace_id(
            batch.batch_id,
            candidate.integration_id,
        )
        branch_ref = deterministic_integration_branch_ref(
            batch.batch_id,
            candidate.integration_id,
        )
        existing = {
            workspace.id: workspace
            for workspace in await self._workspaces.list_workspaces(project_id=project_id)
        }
        workspace = existing.get(workspace_id)
        if workspace is None:
            workspace = await self._workspaces.create_workspace(
                project_id=project_id,
                owner_ref=owner_ref,
                workspace_type=WorkspaceType.ISOLATED_RUN,
                context=data_context,
                access_mode=WorkspaceAccessMode.READ_WRITE,
                retention=WorkspaceRetention.EPHEMERAL,
                source_refs=(
                    WorkspaceSourceRef(
                        kind=WorkspaceSourceKind.REPOSITORY,
                        ref=batch.repository_id,
                        revision=candidate.target_base_revision,
                        metadata={
                            "coding_batch_id": batch.batch_id,
                            "integration_id": candidate.integration_id,
                        },
                    ),
                ),
                workspace_id=workspace_id,
            )
        source_matches = any(
            source.kind is WorkspaceSourceKind.REPOSITORY
            and source.ref == batch.repository_id
            and source.revision == candidate.target_base_revision
            for source in workspace.source_refs
        )
        if not source_matches:
            raise ValueError("recovered integration Workspace has another repository/base revision")

        if workspace.base_snapshot_id is not None:
            snapshot = await self._workspaces.get_snapshot(workspace.base_snapshot_id)
        else:
            snapshot = await self._workspaces.create_snapshot(workspace.id)

        branches = await self._repositories.branches(batch.repository_id, repository_context)
        if branch_ref not in branches:
            created = await self._repositories.create_branch(
                batch.repository_id,
                branch_ref,
                repository_context,
                start_revision=candidate.target_base_revision,
                checkout=False,
            )
            if created.commit_sha != candidate.target_base_revision:
                raise ValueError("#82 created integration branch from another target revision")
        else:
            commits = await self._repositories.commits(
                batch.repository_id,
                repository_context,
                revision=branch_ref,
                limit=1,
            )
            if not commits or commits[0].revision != candidate.target_base_revision:
                raise ValueError("existing integration branch no longer points at expected base")
        return workspace.id, snapshot.id, branch_ref

    @staticmethod
    def _verification_context(
        batch: CodingBatch,
        candidate: IntegrationCandidate,
        slot: IntegrationDispatchSlot,
    ) -> dict[str, JsonValue]:
        return {
            "coding_batch_id": batch.batch_id,
            "integration_id": candidate.integration_id,
            "repository_id": batch.repository_id,
            "target_ref": batch.target_ref,
            "target_base_revision": candidate.target_base_revision,
            "ordered_workstream_ids": list(candidate.ordered_workstream_ids),
            "ordered_revisions": list(candidate.ordered_revisions),
            "plan_id": slot.plan_id,
            "plan_revision": slot.plan_revision,
            "step_id": slot.step_id,
        }

    @staticmethod
    def _validate_agent_run(
        record: AgentRunRecord,
        *,
        slot: IntegrationDispatchSlot,
        agent_id: str,
        revision: int | None,
        verification_context: Mapping[str, JsonValue],
    ) -> None:
        if record.run_id != slot.run_id or record.task_id != slot.task_id:
            raise ValueError("canonical Integration AgentRun is bound to another Task/Run")
        if record.agent.agent_id != agent_id:
            raise ValueError("canonical integration Run is already bound to another Agent")
        if revision is not None and record.agent.revision != revision:
            raise ValueError("canonical integration Run is already bound to another Agent revision")
        if record.team is not None:
            raise ValueError("single-Agent integration cannot reuse an AgentTeam AgentRun")
        for key, expected in verification_context.items():
            if record.verification_context.get(key) != expected:
                raise ValueError("Integration AgentRun lacks exact candidate execution provenance")

    @staticmethod
    def _validate_recorded_execution(
        candidate: IntegrationCandidate,
        slot: IntegrationDispatchSlot,
        agent_run: AgentRunRecord,
    ) -> None:
        execution = candidate.execution
        assert execution is not None
        expected = (
            slot.task_id,
            slot.plan_id,
            slot.plan_revision,
            slot.step_id,
            slot.run_id,
            agent_run.agent_run_id,
            agent_revision_ref(agent_run),
        )
        recorded = (
            execution.task_id,
            execution.plan_id,
            execution.plan_revision,
            execution.step_id,
            execution.run_id,
            execution.agent_run_id,
            execution.agent_revision,
        )
        if recorded != expected:
            raise ValueError("integration replay differs from recorded canonical execution")


__all__ = [
    "CanonicalCodingIntegrationDispatcher",
    "CodingIntegrationDispatch",
    "IntegrationDispatchSlot",
    "IntegrationPlanCoordinationReader",
    "IntegrationRepositoryEvidenceReader",
    "deterministic_integration_branch_ref",
    "deterministic_integration_workspace_id",
]
