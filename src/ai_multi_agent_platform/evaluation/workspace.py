"""Workspace-backed per-attempt evaluation isolation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    WorkspaceFile,
    WorkspaceProvider,
    WorkspaceRetention,
    WorkspaceSourceRef,
    WorkspaceType,
)

from .context import EvaluationExecutionContext
from .models import EvaluationAttempt, EvaluationCase


@dataclass(frozen=True, slots=True)
class EvaluationFixture:
    """Named immutable workspace fixture resolved into canonical file/source references."""

    fixture_id: str
    files: tuple[WorkspaceFile, ...] = ()
    source_refs: tuple[WorkspaceSourceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.fixture_id.strip():
            raise ValueError("evaluation fixture_id must not be blank")
        paths = [entry.relative_path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("evaluation fixture file paths must be unique")


@dataclass(frozen=True, slots=True)
class ResolvedEvaluationFixtures:
    files: tuple[WorkspaceFile, ...] = ()
    source_refs: tuple[WorkspaceSourceRef, ...] = ()


class EvaluationFixtureResolver(Protocol):
    """Resolve case fixture IDs without coupling evaluation cases to one storage backend."""

    async def resolve_fixtures(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> ResolvedEvaluationFixtures: ...


class StaticEvaluationFixtureResolver:
    """Deterministic fixture resolver for checked-in/reference evaluation suites."""

    def __init__(self, fixtures: tuple[EvaluationFixture, ...]) -> None:
        fixture_ids = [fixture.fixture_id for fixture in fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("static evaluation fixture IDs must be unique")
        self._fixtures = {fixture.fixture_id: fixture for fixture in fixtures}

    async def resolve_fixtures(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> ResolvedEvaluationFixtures:
        del attempt
        selected: list[EvaluationFixture] = []
        for fixture_id in case.fixtures:
            try:
                selected.append(self._fixtures[fixture_id])
            except KeyError as exc:
                raise ValueError(f"evaluation fixture not found: {fixture_id}") from exc

        files = tuple(entry for fixture in selected for entry in fixture.files)
        paths = [entry.relative_path for entry in files]
        if len(paths) != len(set(paths)):
            raise ValueError("resolved evaluation fixture file paths must be unique")
        source_refs = tuple(entry for fixture in selected for entry in fixture.source_refs)
        return ResolvedEvaluationFixtures(files=files, source_refs=source_refs)


class WorkspaceEvaluationIsolation:
    """Create a fresh ephemeral isolated-run workspace for every evaluation attempt."""

    def __init__(
        self,
        *,
        workspace_provider: WorkspaceProvider,
        project_id: str,
        owner_ref: OwnerRef,
        actor_ref: str | None = None,
        fixture_resolver: EvaluationFixtureResolver | None = None,
    ) -> None:
        if not project_id.strip():
            raise ValueError("evaluation workspace project_id must not be blank")
        self._workspace_provider = workspace_provider
        self._project_id = project_id
        self._owner_ref = owner_ref
        self._actor_ref = actor_ref or f"{owner_ref.type}:{owner_ref.id}"
        self._fixture_resolver = fixture_resolver
        self._active: dict[str, EvaluationExecutionContext] = {}
        self._lock = asyncio.Lock()

    def _data_context(self, attempt: EvaluationAttempt) -> DataAccessContext:
        return DataAccessContext(
            operation=OperationContext(
                correlation_id=attempt.attempt_id,
                causation_id=attempt.evaluation_run_id,
                owner_type=self._owner_ref.type,
                owner_id=self._owner_ref.id,
                project_id=self._project_id,
            ),
            actor_ref=self._actor_ref,
        )

    async def _fixtures(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> ResolvedEvaluationFixtures:
        if not case.fixtures:
            return ResolvedEvaluationFixtures()
        if self._fixture_resolver is None:
            raise ValueError("evaluation case declares fixtures but no fixture resolver is configured")
        return await self._fixture_resolver.resolve_fixtures(case=case, attempt=attempt)

    async def reset_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        del case
        async with self._lock:
            existing = self._active.pop(attempt.attempt_id, None)
        if existing is not None and existing.workspace_materialization_id is not None:
            await self._workspace_provider.release_materialization(
                existing.workspace_materialization_id,
                MaterializationOutcome.CANCELLED,
            )

    async def setup_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> EvaluationExecutionContext:
        fixtures = await self._fixtures(case=case, attempt=attempt)
        data_context = self._data_context(attempt)
        workspace = await self._workspace_provider.create_workspace(
            project_id=self._project_id,
            owner_ref=self._owner_ref,
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=data_context,
            retention=WorkspaceRetention.EPHEMERAL,
            source_refs=fixtures.source_refs,
            files=fixtures.files,
        )
        if workspace.base_snapshot_id is None:
            raise ValueError("isolated evaluation workspace has no base snapshot")
        snapshot = await self._workspace_provider.get_snapshot(workspace.base_snapshot_id)
        materialization = await self._workspace_provider.materialize(
            workspace.id,
            data_context,
            snapshot_id=snapshot.id,
        )
        execution_context = EvaluationExecutionContext(
            attempt_id=attempt.attempt_id,
            project_id=self._project_id,
            owner_type=self._owner_ref.type,
            owner_id=self._owner_ref.id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            workspace_content_checksum=snapshot.content_checksum,
            workspace_materialization_id=materialization.id,
            execution_workspace=materialization.execution_workspace,
        )
        async with self._lock:
            if attempt.attempt_id in self._active:
                await self._workspace_provider.release_materialization(
                    materialization.id,
                    MaterializationOutcome.CANCELLED,
                )
                raise ValueError(f"evaluation attempt is already isolated: {attempt.attempt_id}")
            self._active[attempt.attempt_id] = execution_context
        return execution_context

    async def teardown_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
        succeeded: bool,
    ) -> None:
        del case
        if execution_context.attempt_id != attempt.attempt_id:
            raise ValueError("evaluation teardown context belongs to another attempt")
        async with self._lock:
            active = self._active.pop(attempt.attempt_id, None)
        if active is None:
            raise ValueError(f"evaluation attempt has no active workspace: {attempt.attempt_id}")
        if active != execution_context:
            raise ValueError("evaluation teardown context does not match active workspace")
        materialization_id = execution_context.workspace_materialization_id
        if materialization_id is None:
            raise ValueError("workspace-backed evaluation context has no materialization")
        await self._workspace_provider.release_materialization(
            materialization_id,
            MaterializationOutcome.SUCCEEDED if succeeded else MaterializationOutcome.FAILED,
        )
