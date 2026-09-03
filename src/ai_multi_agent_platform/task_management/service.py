"""Platform-owned Task planning service layered on the canonical event-sourced kernel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import TaskStatus, validate_id
from ai_multi_agent_platform.kernel import PlatformKernel, TaskState

from .models import (
    TASK_MANAGEMENT_METADATA_KEY,
    TaskDependencyKind,
    TaskPlanningMetadata,
)

WorkspaceProjectResolver = Callable[[str], str | Awaitable[str]]
NowProvider = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class TaskManagementView:
    task_id: str
    metadata: TaskPlanningMetadata
    blocking_task_ids: tuple[str, ...]
    failed_dependency_ids: tuple[str, ...]
    overdue: bool
    not_before_blocked: bool
    blocked: bool
    eligible: bool

    def planning_resource(self) -> dict[str, JsonValue]:
        responsibility = self.metadata.responsibility
        assignment = self.metadata.agent_assignment
        blocking_reason = self.metadata.blocking_reason
        if blocking_reason is None and self.failed_dependency_ids:
            blocking_reason = "prerequisite_failed_or_cancelled"
        elif blocking_reason is None and self.blocking_task_ids:
            blocking_reason = "prerequisite_incomplete"
        elif blocking_reason is None and self.not_before_blocked:
            blocking_reason = "not_before"
        elif blocking_reason is None and self.metadata.archived:
            blocking_reason = "archived"
        return {
            **self.metadata.to_json(),
            "priority_rank": self.metadata.priority.rank,
            "responsible_type": responsibility.kind if responsibility else None,
            "responsible_id": responsibility.id if responsibility else None,
            "agent_assignment_type": assignment.kind if assignment else None,
            "agent_assignment_id": assignment.id if assignment else None,
            "blocking_task_ids": list(self.blocking_task_ids),
            "failed_dependency_ids": list(self.failed_dependency_ids),
            "overdue": self.overdue,
            "not_before_blocked": self.not_before_blocked,
            "blocked": self.blocked,
            "eligible": self.eligible,
            "effective_blocking_reason": blocking_reason,
        }


@dataclass(frozen=True, slots=True)
class PreparedTaskManagementUpdate:
    task: TaskState
    metadata: TaskPlanningMetadata


class TaskManagementService:
    """Adds planning semantics without owning a second Task record or lifecycle."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        workspace_project_resolver: WorkspaceProjectResolver | None = None,
        now: NowProvider | None = None,
    ) -> None:
        self._kernel = kernel
        self._workspace_project_resolver = workspace_project_resolver
        self._now = now or (lambda: datetime.now(UTC))

    async def validate_new(
        self,
        *,
        task_id: str,
        project_id: str | None,
        changes: Mapping[str, JsonValue],
    ) -> TaskPlanningMetadata:
        """Validate planning metadata before the canonical Task creation is committed."""

        try:
            validate_id(task_id, "task")
            metadata = TaskPlanningMetadata().patch(changes)
            if metadata.parent_task_id is not None:
                if metadata.parent_task_id == task_id:
                    raise ValueError("task cannot be its own parent")
                parent = await self._kernel.get_task(metadata.parent_task_id)
                self._require_same_project(task_id, project_id, parent)
            if metadata.workspace_id is not None and self._workspace_project_resolver is not None:
                if await self._resolve_workspace_project(metadata.workspace_id) != project_id:
                    raise ValueError("workspace must belong to the task project")
            for dependency in metadata.dependencies:
                if dependency.task_id == task_id:
                    raise ValueError("task cannot depend on or relate to itself")
                prerequisite = await self._kernel.get_task(dependency.task_id)
                self._require_same_project(task_id, project_id, prerequisite)
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
        return metadata

    async def get(self, task_id: str) -> TaskManagementView:
        return await self.view(await self._kernel.get_task(task_id))

    async def view(self, task: TaskState) -> TaskManagementView:
        metadata = self.metadata_for(task)
        blocking: list[str] = []
        failed: list[str] = []
        for dependency in metadata.dependencies:
            if dependency.kind is not TaskDependencyKind.DEPENDS_ON:
                continue
            prerequisite = await self._kernel.get_task(dependency.task_id)
            if prerequisite.status is TaskStatus.SUCCEEDED:
                continue
            blocking.append(dependency.task_id)
            if prerequisite.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                failed.append(dependency.task_id)

        now = self._aware_now()
        not_before_blocked = metadata.not_before is not None and now < metadata.not_before
        overdue = (
            metadata.due_at is not None
            and now > metadata.due_at
            and task.status not in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}
        )
        blocked = bool(
            blocking or metadata.blocking_reason or not_before_blocked or metadata.archived
        )
        return TaskManagementView(
            task_id=task.task_id,
            metadata=metadata,
            blocking_task_ids=tuple(blocking),
            failed_dependency_ids=tuple(failed),
            overdue=overdue,
            not_before_blocked=not_before_blocked,
            blocked=blocked,
            eligible=not blocked,
        )

    def metadata_for(self, task: TaskState) -> TaskPlanningMetadata:
        return TaskPlanningMetadata.from_json(task.task.metadata.get(TASK_MANAGEMENT_METADATA_KEY))

    async def prepare(
        self,
        task_id: str,
        changes: Mapping[str, JsonValue],
        *,
        overrides: Mapping[str, TaskPlanningMetadata] | None = None,
    ) -> PreparedTaskManagementUpdate:
        task = await self._kernel.get_task(task_id)
        current = self.metadata_for(task)
        try:
            candidate = current.patch(changes)
            await self._validate_relations(task, candidate)
            graph_overrides = dict(overrides or {})
            graph_overrides[task_id] = candidate
            await self._validate_no_cycles(task_id, graph_overrides)
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
        return PreparedTaskManagementUpdate(task=task, metadata=candidate)

    async def prepare_batch(
        self,
        updates: Sequence[tuple[str, Mapping[str, JsonValue]]],
    ) -> tuple[PreparedTaskManagementUpdate, ...]:
        candidates: dict[str, TaskPlanningMetadata] = {}
        prepared: list[PreparedTaskManagementUpdate] = []
        for task_id, changes in updates:
            task = await self._kernel.get_task(task_id)
            current = candidates.get(task_id, self.metadata_for(task))
            try:
                candidate = current.patch(changes)
                await self._validate_relations(task, candidate)
            except ValueError as exc:
                raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
            candidates[task_id] = candidate
            prepared.append(PreparedTaskManagementUpdate(task=task, metadata=candidate))
        for task_id in candidates:
            try:
                await self._validate_no_cycles(task_id, candidates)
            except ValueError as exc:
                raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
        return tuple(prepared)

    async def commit(
        self,
        prepared: PreparedTaskManagementUpdate,
        *,
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-management",
    ) -> TaskManagementView:
        metadata: dict[str, JsonValue] = {TASK_MANAGEMENT_METADATA_KEY: prepared.metadata.to_json()}
        if prepared.task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            # Lifecycle-terminal Tasks still accept planning-only metadata such as
            # archived/hidden state. Use the kernel's canonical command/event path
            # directly so lifecycle state stays immutable while audit/idempotency
            # and event mirroring remain identical to ordinary task.updated events.
            await self._kernel._commit_task_command(
                task=prepared.task,
                key=idempotency_key,
                operation="update_task",
                event_specs=(
                    (
                        "task.updated",
                        "task",
                        prepared.task.task_id,
                        {"metadata": metadata},
                        (),
                    ),
                ),
                result_id=prepared.task.task_id,
                actor_ref=actor_ref,
                source=source,
            )
        else:
            await self._kernel.update_task(
                idempotency_key=idempotency_key,
                task_id=prepared.task.task_id,
                metadata=metadata,
                actor_ref=actor_ref,
                source=source,
            )
        return await self.get(prepared.task.task_id)

    async def update(
        self,
        task_id: str,
        changes: Mapping[str, JsonValue],
        *,
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-management",
    ) -> TaskManagementView:
        prepared = await self.prepare(task_id, changes)
        return await self.commit(
            prepared,
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            source=source,
        )

    async def require_eligible(self, task_id: str) -> TaskManagementView:
        view = await self.get(task_id)
        if view.eligible:
            return view
        details: dict[str, JsonValue] = {
            "task_id": task_id,
            "blocking_task_ids": list(view.blocking_task_ids),
            "failed_dependency_ids": list(view.failed_dependency_ids),
            "not_before": (
                view.metadata.not_before.isoformat()
                if view.metadata.not_before is not None
                else None
            ),
            "archived": view.metadata.archived,
            "blocking_reason": view.metadata.blocking_reason,
        }
        raise ContractError(
            ErrorCode.CONFLICT,
            f"task {task_id} is not eligible for normal progression",
            details=details,
        )

    async def _validate_relations(
        self,
        task: TaskState,
        metadata: TaskPlanningMetadata,
    ) -> None:
        task_id = task.task_id
        project_id = task.task.project_id
        if metadata.parent_task_id is not None:
            if metadata.parent_task_id == task_id:
                raise ValueError("task cannot be its own parent")
            parent = await self._kernel.get_task(metadata.parent_task_id)
            self._require_same_project(task_id, project_id, parent)
        if metadata.workspace_id is not None and self._workspace_project_resolver is not None:
            workspace_project_id = await self._resolve_workspace_project(metadata.workspace_id)
            if workspace_project_id != project_id:
                raise ValueError("workspace must belong to the task project")
        seen: set[str] = set()
        for dependency in metadata.dependencies:
            if dependency.task_id == task_id:
                raise ValueError("task cannot depend on or relate to itself")
            marker = f"{dependency.kind.value}:{dependency.task_id}"
            if marker in seen:
                raise ValueError("duplicate task dependency")
            seen.add(marker)
            prerequisite = await self._kernel.get_task(dependency.task_id)
            self._require_same_project(task_id, project_id, prerequisite)

    async def _resolve_workspace_project(self, workspace_id: str) -> str:
        resolver = self._workspace_project_resolver
        if resolver is None:
            raise RuntimeError("workspace resolver is not configured")
        project = resolver(workspace_id)
        if isinstance(project, str):
            return project
        return await project

    @staticmethod
    def _require_same_project(
        task_id: str,
        project_id: str | None,
        related: TaskState,
    ) -> None:
        if related.task.project_id != project_id:
            raise ValueError(
                f"cross-project task relation is not allowed: {task_id} -> {related.task_id}"
            )

    async def _validate_no_cycles(
        self,
        root_task_id: str,
        overrides: Mapping[str, TaskPlanningMetadata],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        async def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError(f"task dependency cycle detected at {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            metadata = overrides.get(task_id)
            if metadata is None:
                metadata = self.metadata_for(await self._kernel.get_task(task_id))
            for dependency in metadata.dependencies:
                if dependency.kind is TaskDependencyKind.DEPENDS_ON:
                    await visit(dependency.task_id)
            visiting.remove(task_id)
            visited.add(task_id)

        await visit(root_task_id)

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("task-management clock must return a timezone-aware datetime")
        return value
