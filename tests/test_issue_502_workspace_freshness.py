from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts import ExecutionRequest as KernelExecutionRequest
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import ToolInvocation
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.repositories import (
    RepositoryProvenanceStore,
    RepositoryTree,
    RepositoryTreeEntry,
    RepositoryWorkspaceExecutionCoordinator,
)
from ai_multi_agent_platform.repository_intelligence import (
    RepositoryIntelligenceFreshness,
    WorkspaceAwareRepositoryIntelligenceProvider,
)
from ai_multi_agent_platform.repository_intelligence.wiring import (
    AuthorizedRunWorkspaceSnapshotLoader,
)
from ai_multi_agent_platform.security import AuthorizationGate
from ai_multi_agent_platform.testing import FakeAuthorizationProvider
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    RunWorkspaceBinding,
    WorkspaceFile,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)

_BASE_REVISION = "a" * 40
_OTHER_REVISION = "b" * 40


async def _bound_stack(
    tmp_path: Path,
    *,
    authorization_allowed: bool = True,
    max_entries: int = 5_000,
    max_total_bytes: int = 64 * 1024 * 1024,
) -> tuple[
    str,
    str,
    str,
    str,
    OperationContext,
    LocalWorkspaceProvider,
    RepositoryWorkspaceExecutionCoordinator,
    WorkspaceAwareRepositoryIntelligenceProvider,
    FakeAuthorizationProvider,
]:
    project_id = new_id("project")
    task_id = new_id("task")
    run_id = new_id("run")
    actor_ref = new_id("user")
    repository_id = new_id("external_resource")
    operation = OperationContext(
        correlation_id=task_id,
        owner_type="user",
        owner_id=actor_ref,
        project_id=project_id,
    )
    data_context = DataAccessContext(
        operation=operation,
        actor_ref=actor_ref,
        task_id=task_id,
        run_id=run_id,
    )

    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
    record = await files.create_file(b"base needle\n", data_context)
    workspaces = LocalWorkspaceProvider(tmp_path / "materializations", files)
    workspace = await workspaces.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="user", id=actor_ref),
        workspace_type=WorkspaceType.ISOLATED_RUN,
        context=data_context,
        source_refs=(
            WorkspaceSourceRef(
                kind=WorkspaceSourceKind.REPOSITORY,
                ref=repository_id,
                revision=_BASE_REVISION,
                metadata={"requested_revision": "main", "provider_id": "local-git"},
            ),
        ),
        files=(
            WorkspaceFile(
                relative_path="src/demo.py",
                file_id=record.file_id,
                sha256=record.sha256,
            ),
        ),
    )
    assert workspace.base_snapshot_id is not None
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

    bindings = InMemoryRunWorkspaceBindingRepository()
    await bindings.bind(
        RunWorkspaceBinding(
            run_id=run_id,
            task_id=task_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            content_checksum=snapshot.content_checksum,
        )
    )
    coordinator = RepositoryWorkspaceExecutionCoordinator(
        bindings,
        workspaces,
        RepositoryProvenanceStore(),
        fallback_workspace="reference",
    )
    authorization = FakeAuthorizationProvider(allowed=authorization_allowed)
    workspace_loader = AuthorizedRunWorkspaceSnapshotLoader(
        bindings,
        workspaces,
        files,
        coordinator,
        AuthorizationGate(authorization),
        actor_resolver=lambda context: context.owner_id or "",
        max_entries=max_entries,
        max_total_bytes=max_total_bytes,
    )

    async def fallback_loader(
        requested_repository_id: str,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        del context
        resolved = _OTHER_REVISION if revision == "other" else _BASE_REVISION
        return RepositoryTree(
            repository_id=requested_repository_id,
            requested_ref=revision,
            resolved_revision=resolved,
            entries=(RepositoryTreeEntry("fallback.txt", b"fallback needle\n"),),
        )

    provider = WorkspaceAwareRepositoryIntelligenceProvider(
        fallback_loader,
        workspace_loader,
    )
    return (
        repository_id,
        task_id,
        run_id,
        actor_ref,
        operation,
        workspaces,
        coordinator,
        provider,
        authorization,
    )


def _search_invocation(
    *,
    repository_id: str,
    task_id: str,
    run_id: str,
    operation: OperationContext,
    revision: str = "HEAD",
    query: str = "needle",
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="issue-502-search",
        tool_ref="repository.text_search",
        arguments={
            "repository_id": repository_id,
            "revision": revision,
            "query": query,
        },
        context=operation,
        task_id=task_id,
        run_id=run_id,
    )


def test_run_bound_repository_intelligence_uses_immutable_workspace_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            repository_id,
            task_id,
            run_id,
            _actor_ref,
            operation,
            _workspaces,
            _coordinator,
            provider,
            authorization,
        ) = await _bound_stack(tmp_path)

        result = await provider.invoke(
            _search_invocation(
                repository_id=repository_id,
                task_id=task_id,
                run_id=run_id,
                operation=operation,
                query="base needle",
            )
        )
        assert isinstance(result.output, dict)
        provenance = result.output["provenance"]
        assert isinstance(provenance, dict)
        workspace = provenance["workspace"]
        assert isinstance(workspace, dict)
        assert provenance["resolved_revision"] == _BASE_REVISION
        assert provenance["freshness"] == RepositoryIntelligenceFreshness.WORKSPACE_SNAPSHOT.value
        assert provenance["source_kind"] == "workspace_snapshot"
        assert workspace["dirty"] is False
        assert workspace["materialization_id"] is None
        assert len(authorization.calls) == 1
        assert authorization.calls[0].resource_type == "workspace"
        assert authorization.calls[0].action == "read"

    asyncio.run(scenario())


def test_run_bound_repository_intelligence_reads_dirty_live_materialization(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            repository_id,
            task_id,
            run_id,
            _actor_ref,
            operation,
            workspaces,
            coordinator,
            provider,
            _authorization,
        ) = await _bound_stack(tmp_path)

        execution_workspace = await coordinator.resolve_execution_workspace(
            KernelExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=operation,
            )
        )
        source = workspaces.materialization_root / execution_workspace / "src" / "demo.py"
        source.write_text("dirty live needle\n", encoding="utf-8")

        result = await provider.invoke(
            _search_invocation(
                repository_id=repository_id,
                task_id=task_id,
                run_id=run_id,
                operation=operation,
                query="dirty live needle",
            )
        )
        assert isinstance(result.output, dict)
        assert result.output["hits"]
        provenance = result.output["provenance"]
        assert isinstance(provenance, dict)
        workspace = provenance["workspace"]
        assert isinstance(workspace, dict)
        assert provenance["resolved_revision"] == _BASE_REVISION
        assert provenance["freshness"] == RepositoryIntelligenceFreshness.LIVE_WORKSPACE.value
        assert provenance["source_kind"] == "workspace_materialization"
        assert workspace["dirty"] is True
        assert isinstance(workspace["materialization_id"], str)
        assert workspace["source_content_checksum"] != workspace["workspace_snapshot_checksum"]

    asyncio.run(scenario())


def test_workspace_repository_intelligence_denies_unauthorized_read_before_source_access(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            repository_id,
            task_id,
            run_id,
            _actor_ref,
            operation,
            _workspaces,
            _coordinator,
            provider,
            authorization,
        ) = await _bound_stack(tmp_path, authorization_allowed=False)

        with pytest.raises(ContractError) as caught:
            await provider.invoke(
                _search_invocation(
                    repository_id=repository_id,
                    task_id=task_id,
                    run_id=run_id,
                    operation=operation,
                )
            )
        assert caught.value.code is ErrorCode.FORBIDDEN
        assert len(authorization.calls) == 1
        assert authorization.calls[0].resource_ref.startswith("workspace_")

    asyncio.run(scenario())


def test_explicit_other_revision_does_not_get_replaced_by_run_workspace(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            repository_id,
            task_id,
            run_id,
            _actor_ref,
            operation,
            _workspaces,
            _coordinator,
            provider,
            authorization,
        ) = await _bound_stack(tmp_path)

        result = await provider.invoke(
            _search_invocation(
                repository_id=repository_id,
                task_id=task_id,
                run_id=run_id,
                operation=operation,
                revision="other",
                query="fallback needle",
            )
        )
        assert isinstance(result.output, dict)
        provenance = result.output["provenance"]
        assert isinstance(provenance, dict)
        assert provenance == {
            "repository_id": repository_id,
            "requested_revision": "other",
            "resolved_revision": _OTHER_REVISION,
            "intelligence_provider_id": "platform.repository-intelligence.baseline",
            "freshness": RepositoryIntelligenceFreshness.LIVE_REVISION.value,
        }
        assert authorization.calls == []

    asyncio.run(scenario())


def test_dirty_live_workspace_source_is_resource_bounded(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            repository_id,
            task_id,
            run_id,
            _actor_ref,
            operation,
            _workspaces,
            coordinator,
            provider,
            _authorization,
        ) = await _bound_stack(tmp_path, max_total_bytes=4)
        await coordinator.resolve_execution_workspace(
            KernelExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=operation,
            )
        )

        with pytest.raises(ContractError) as caught:
            await provider.invoke(
                _search_invocation(
                    repository_id=repository_id,
                    task_id=task_id,
                    run_id=run_id,
                    operation=operation,
                )
            )
        assert caught.value.code is ErrorCode.RESOURCE_EXHAUSTED

    asyncio.run(scenario())
