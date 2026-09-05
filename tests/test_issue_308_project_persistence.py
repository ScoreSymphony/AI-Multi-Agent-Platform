from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ScopeStore
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Project, Provenance, new_id


def _complete_project() -> Project:
    return Project(
        id=new_id("project"),
        name="Portable Project",
        owner_ref=OwnerRef(type="user", id="user-owner"),
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        updated_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
        schema_version="1.0",
        provenance=Provenance(
            source="import:test",
            actor_ref="user:user-owner",
            details={
                "nested": {"list": [1, "two", True]},
                "revision": 7,
            },
        ),
        external_refs=(
            ExternalRef(system="git", kind="repository", value="project/repo"),
            ExternalRef(system="catalog", kind="record", value="42"),
        ),
    )


def test_complete_project_snapshot_round_trips_in_memory() -> None:
    store = ScopeStore()
    project = _complete_project()

    stored = store.store_project_snapshot(key="import-1", project=project)

    assert stored == project
    assert store.get_project(project.id) == project
    assert store.store_project_snapshot(key="import-1", project=project) == project


def test_complete_project_snapshot_round_trips_through_sqlite_restart(tmp_path) -> None:
    path = tmp_path / "scopes.sqlite3"
    project = _complete_project()
    store = SqliteScopeStore(path)
    store.store_project_snapshot(key="import-1", project=project)

    restored = SqliteScopeStore(path).get_project(project.id)

    assert restored == project
    assert dict(restored.provenance.details) == dict(project.provenance.details)  # type: ignore[union-attr]
    assert restored.external_refs == project.external_refs


def test_prior_sqlite_schema_migrates_with_safe_empty_metadata(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    project_id = new_id("project")
    created_at = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE scope_projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO scope_projects VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, "Legacy", "user", "legacy-owner", created_at, created_at, "1.0"),
        )

    store = SqliteScopeStore(path)
    restored = store.get_project(project_id)

    assert restored.id == project_id
    assert restored.provenance is None
    assert restored.external_refs == ()
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(scope_projects)")}
    assert {"provenance_json", "external_refs_json"}.issubset(columns)


@pytest.mark.parametrize("store_factory", [ScopeStore, lambda: None])
def test_compensation_requires_explicit_cross_domain_safety_proof(store_factory, tmp_path) -> None:
    store = (
        SqliteScopeStore(tmp_path / "scopes.sqlite3")
        if store_factory() is None
        else store_factory()
    )
    project = _complete_project()
    store.store_project_snapshot(key="import-1", project=project)

    with pytest.raises(ContractError) as exc_info:
        store.compensate_project(project.id)

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert store.get_project(project.id) == project


@pytest.mark.parametrize("sqlite", [False, True])
def test_compensation_removes_only_fresh_unreferenced_project(sqlite, tmp_path) -> None:
    store = SqliteScopeStore(tmp_path / "scopes.sqlite3") if sqlite else ScopeStore()
    project = _complete_project()
    store.store_project_snapshot(key="import-1", project=project)

    removed = store.compensate_project(project.id, external_dependencies=())

    assert removed == project
    with pytest.raises(ContractError) as exc_info:
        store.get_project(project.id)
    assert exc_info.value.code is ErrorCode.NOT_FOUND
    if sqlite:
        restarted = SqliteScopeStore(tmp_path / "scopes.sqlite3")
        with pytest.raises(ContractError) as restarted_exc:
            restarted.get_project(project.id)
        assert restarted_exc.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize("sqlite", [False, True])
def test_compensation_refuses_external_canonical_dependencies(sqlite, tmp_path) -> None:
    store = SqliteScopeStore(tmp_path / "scopes.sqlite3") if sqlite else ScopeStore()
    project = _complete_project()
    store.store_project_snapshot(key="import-1", project=project)

    with pytest.raises(ContractError) as exc_info:
        store.compensate_project(
            project.id,
            external_dependencies=("task:task-dependent",),
        )

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert store.get_project(project.id) == project


@pytest.mark.parametrize("sqlite", [False, True])
def test_compensation_refuses_workspace_dependency(sqlite, tmp_path) -> None:
    store = SqliteScopeStore(tmp_path / "scopes.sqlite3") if sqlite else ScopeStore()
    project = _complete_project()
    store.store_project_snapshot(key="import-1", project=project)
    workspace = store.create_workspace(key="workspace-1", project_id=project.id)

    with pytest.raises(ContractError) as exc_info:
        store.compensate_project(project.id, external_dependencies=())

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert workspace.id in str(exc_info.value.details)
    assert store.get_project(project.id) == project
