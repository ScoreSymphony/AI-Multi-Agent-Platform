"""Canonical Task Project reassignment semantics owned by the Task-management domain.

The service preflights canonical relationship/workspace invariants, ownership boundaries and active
execution before asking the platform kernel to append the move event.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Project, TaskStatus, validate_id
from ai_multi_agent_platform.kernel import (
    TERMINAL_RUN_STATUSES,
    PlatformKernel,
    TaskMutationBoundary,
    TaskState,
)
from ai_multi_agent_platform.organizations import (
    MembershipStatus,
    OrganizationService,
    OrganizationStatus,
    ShareStatus,
    TeamStatus,
)

from .service import TaskManagementService

ProjectResolver = Callable[[str], Project | Awaitable[Project]]
WorkspaceProjectResolver = Callable[[str], str | Awaitable[str]]
TaskIdProvider = Callable[[], tuple[str, ...] | Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class TaskProjectMoveRequest:
    task_id: str
    destination_project_id: str | None

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        if self.destination_project_id is not None:
            validate_id(self.destination_project_id, "project")


@dataclass(frozen=True, slots=True)
class PreparedTaskProjectMove:
    task: TaskState
    source_project: Project | None
    destination_project: Project | None

    @property
    def destination_project_id(self) -> str | None:
        return None if self.destination_project is None else self.destination_project.id


class TaskProjectCompatibilityPolicy(Protocol):
    async def require_compatible(
        self,
        *,
        task: TaskState,
        source_project: Project | None,
        destination_project: Project | None,
    ) -> None: ...


class DefaultTaskProjectCompatibilityPolicy:
    """Fail-closed ownership boundary with optional collaboration semantics.

    Membership is directional: it can establish that a personal user/service scope may enter an
    active Organization/Team scope. It does not make that actor's personal resources interchangeable
    with Organization-owned resources in the reverse direction. Other owner changes require explicit
    Project sharing.
    """

    def __init__(self, organizations: OrganizationService | None = None) -> None:
        self._organizations = organizations

    async def require_compatible(
        self,
        *,
        task: TaskState,
        source_project: Project | None,
        destination_project: Project | None,
    ) -> None:
        source_owner = task.task.owner_ref if source_project is None else source_project.owner_ref
        destination_owner = (
            task.task.owner_ref if destination_project is None else destination_project.owner_ref
        )
        if source_owner == destination_owner:
            return

        organizations = self._organizations
        if organizations is None:
            self._raise_incompatible(source_owner, destination_owner)

        assert organizations is not None
        source_organization = await self._structured_organization(source_owner)
        destination_organization = await self._structured_organization(destination_owner)

        if (
            source_organization is not None
            and destination_organization is not None
            and source_organization == destination_organization
        ):
            return

        if (
            source_owner.type in {"user", "service"}
            and destination_organization is not None
            and await self._has_active_membership(
                source_owner.id,
                destination_organization,
            )
        ):
            return

        if source_project is not None and destination_project is not None:
            source_shared = await self._project_shared_to(source_project.id, destination_owner)
            destination_shared = await self._project_shared_to(destination_project.id, source_owner)
            if source_shared and destination_shared:
                return

        self._raise_incompatible(source_owner, destination_owner)

    async def _structured_organization(self, owner: OwnerRef) -> str | None:
        organizations = self._organizations
        assert organizations is not None
        if owner.type == "organization":
            organization = await organizations.repository.get_organization(owner.id)
            if organization.status is not OrganizationStatus.ACTIVE:
                return None
            return organization.id
        if owner.type == "team":
            team = await organizations.repository.get_team(owner.id)
            if team.status is not TeamStatus.ACTIVE:
                return None
            organization = await organizations.repository.get_organization(team.organization_id)
            if organization.status is not OrganizationStatus.ACTIVE:
                return None
            return team.organization_id
        return None

    async def _has_active_membership(self, actor_id: str, organization_id: str) -> bool:
        organizations = self._organizations
        assert organizations is not None
        memberships = await organizations.repository.list_memberships(actor_id=actor_id)
        return any(
            membership.organization_id == organization_id
            and membership.status is MembershipStatus.ACTIVE
            for membership in memberships
        )

    async def _project_shared_to(self, project_id: str, target: OwnerRef) -> bool:
        organizations = self._organizations
        assert organizations is not None
        try:
            ownership = await organizations.repository.get_ownership("project", project_id)
        except LookupError:
            return False
        if ownership.owner_ref == target:
            return True
        return any(
            share.status is ShareStatus.ACTIVE and share.target_ref == target
            for share in await organizations.repository.list_shares(ownership.id)
        )

    @staticmethod
    def _raise_incompatible(source: OwnerRef, destination: OwnerRef) -> None:
        raise ContractError(
            ErrorCode.CONFLICT,
            "source and destination Project ownership scopes are incompatible",
            details={
                "source_owner": {"type": source.type, "id": source.id},
                "destination_owner": {"type": destination.type, "id": destination.id},
            },
        )


class TaskProjectReassignmentService:
    """Preflight and commit canonical Task Project moves."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        task_management: TaskManagementService,
        project_resolver: ProjectResolver,
        workspace_project_resolver: WorkspaceProjectResolver,
        task_ids: TaskIdProvider,
        compatibility: TaskProjectCompatibilityPolicy | None = None,
    ) -> None:
        self._kernel = kernel
        self._mutations = TaskMutationBoundary(kernel)
        self._task_management = task_management
        self._project_resolver = project_resolver
        self._workspace_project_resolver = workspace_project_resolver
        self._task_ids = task_ids
        self._compatibility = compatibility or DefaultTaskProjectCompatibilityPolicy()

    async def prepare(self, request: TaskProjectMoveRequest) -> PreparedTaskProjectMove:
        return (await self.prepare_batch((request,)))[0]

    async def prepare_batch(
        self,
        requests: Sequence[TaskProjectMoveRequest],
    ) -> tuple[PreparedTaskProjectMove, ...]:
        destinations = self._batch_destinations(requests)

        states = await self._load_task_graph()
        prepared: list[PreparedTaskProjectMove] = []
        for request in requests:
            task = states.get(request.task_id)
            if task is None:
                task = await self._kernel.get_task(request.task_id)
                states[request.task_id] = task
            if task.task.project_id == request.destination_project_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"Task {request.task_id} is already assigned to the requested Project scope",
                )
            if task.status in {TaskStatus.RUNNING, TaskStatus.WAITING}:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    (
                        f"Task {request.task_id} cannot move while lifecycle state "
                        f"is {task.status.value}"
                    ),
                )
            for run_id in task.run_ids:
                run = await self._kernel.get_run(task.task_id, run_id)
                if run.status not in TERMINAL_RUN_STATUSES:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        f"Task {request.task_id} cannot move while Run {run_id} is active",
                    )

            source_project = await self._project(task.task.project_id)
            destination_project = await self._project(request.destination_project_id)
            await self._compatibility.require_compatible(
                task=task,
                source_project=source_project,
                destination_project=destination_project,
            )
            prepared.append(
                PreparedTaskProjectMove(
                    task=task,
                    source_project=source_project,
                    destination_project=destination_project,
                )
            )

        await self._validate_relationships(states, destinations)
        return tuple(prepared)

    async def batch_reserved(
        self,
        requests: Sequence[TaskProjectMoveRequest],
        *,
        idempotency_key: str,
    ) -> bool:
        digest, _ = self._batch_contract(requests)
        return await self._mutations.bulk_project_reassignment_reserved(
            batch_digest=digest,
            idempotency_key=idempotency_key,
        )

    async def reserve_batch(
        self,
        requests: Sequence[TaskProjectMoveRequest],
        *,
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-project-reassignment",
    ) -> None:
        digest, moves = self._batch_contract(requests)
        if await self._mutations.bulk_project_reassignment_reserved(
            batch_digest=digest,
            idempotency_key=idempotency_key,
        ):
            return

        anchor_id = moves[0]["task_id"]
        assert isinstance(anchor_id, str)
        await self._mutations.reserve_bulk_project_reassignment(
            anchor_task_id=anchor_id,
            batch_digest=digest,
            moves=moves,
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            source=source,
        )

    async def replayed_move(
        self,
        request: TaskProjectMoveRequest,
        *,
        idempotency_key: str,
    ) -> TaskState | None:
        return await self._mutations.replayed_project_reassignment(
            task_id=request.task_id,
            destination_project_id=request.destination_project_id,
            idempotency_key=idempotency_key,
        )

    async def commit(
        self,
        prepared: PreparedTaskProjectMove,
        *,
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-project-reassignment",
    ) -> TaskState:
        return await self._mutations.reassign_project(
            task_id=prepared.task.task_id,
            source_project_id=prepared.task.task.project_id,
            destination_project_id=prepared.destination_project_id,
            expected_revision=prepared.task.revision,
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            source=source,
        )

    async def move(
        self,
        request: TaskProjectMoveRequest,
        *,
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-project-reassignment",
    ) -> TaskState:
        replayed = await self.replayed_move(request, idempotency_key=idempotency_key)
        if replayed is not None:
            return replayed
        return await self.commit(
            await self.prepare(request),
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            source=source,
        )

    def _batch_contract(
        self,
        requests: Sequence[TaskProjectMoveRequest],
    ) -> tuple[str, list[dict[str, JsonValue]]]:
        destinations = self._batch_destinations(requests)
        moves: list[dict[str, JsonValue]] = [
            {
                "task_id": task_id,
                "destination_project_id": destinations[task_id],
            }
            for task_id in sorted(destinations)
        ]
        encoded = json.dumps(
            moves,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest(), moves

    @staticmethod
    def _batch_destinations(
        requests: Sequence[TaskProjectMoveRequest],
    ) -> dict[str, str | None]:
        if not requests:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Task Project move batch is empty")
        if len(requests) > 100:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Task Project move batches are limited to 100 Tasks",
            )
        destinations: dict[str, str | None] = {}
        for request in requests:
            if request.task_id in destinations:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    f"duplicate Task in Project move batch: {request.task_id}",
                )
            destinations[request.task_id] = request.destination_project_id
        return destinations

    async def _validate_relationships(
        self,
        states: Mapping[str, TaskState],
        destinations: Mapping[str, str | None],
    ) -> None:
        moved = set(destinations)
        for task_id, task in states.items():
            metadata = self._task_management.metadata_for(task)
            effective_project = destinations.get(task_id, task.task.project_id)
            related_ids: list[str] = []
            if metadata.parent_task_id is not None:
                related_ids.append(metadata.parent_task_id)
            related_ids.extend(item.task_id for item in metadata.dependencies)

            for related_id in related_ids:
                if task_id not in moved and related_id not in moved:
                    continue
                related = states.get(related_id)
                if related is None:
                    related = await self._kernel.get_task(related_id)
                related_project = destinations.get(related_id, related.task.project_id)
                if effective_project != related_project:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "Task Project move would create a cross-Project Task relationship",
                        details={
                            "task_id": task_id,
                            "task_project_id": effective_project,
                            "related_task_id": related_id,
                            "related_project_id": related_project,
                        },
                    )
                if len(moved) > 1 and task_id in moved and related_id in moved:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "connected Tasks cannot be bulk-moved without multi-stream atomic commits",
                        details={
                            "task_id": task_id,
                            "related_task_id": related_id,
                            "required_capability": "multi_stream_atomic_commit",
                        },
                    )

            if task_id in moved and metadata.workspace_id is not None:
                workspace_project = await self._resolve_workspace_project(metadata.workspace_id)
                if workspace_project != effective_project:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "Task Project move is incompatible with its canonical Workspace",
                        details={
                            "task_id": task_id,
                            "workspace_id": metadata.workspace_id,
                            "workspace_project_id": workspace_project,
                            "destination_project_id": effective_project,
                        },
                    )

    async def _load_task_graph(self) -> dict[str, TaskState]:
        raw = self._task_ids()
        task_ids = await raw if not isinstance(raw, tuple) else raw
        return {task_id: await self._kernel.get_task(task_id) for task_id in task_ids}

    async def _project(self, project_id: str | None) -> Project | None:
        if project_id is None:
            return None
        raw = self._project_resolver(project_id)
        return await raw if not isinstance(raw, Project) else raw

    async def _resolve_workspace_project(self, workspace_id: str) -> str:
        raw = self._workspace_project_resolver(workspace_id)
        return await raw if not isinstance(raw, str) else raw


__all__ = [
    "DefaultTaskProjectCompatibilityPolicy",
    "PreparedTaskProjectMove",
    "TaskProjectCompatibilityPolicy",
    "TaskProjectMoveRequest",
    "TaskProjectReassignmentService",
]
