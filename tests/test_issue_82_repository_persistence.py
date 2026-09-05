from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform import repositories
from ai_multi_agent_platform.domain import new_id


_INPUT_REVISION = "1" * 40
_OUTPUT_REVISION = "2" * 40


def test_sqlite_repository_provenance_survives_restart_and_upsert(tmp_path: Path) -> None:
    path = tmp_path / "repository-provenance.sqlite3"
    run_id = new_id("run")
    task_id = new_id("task")
    repository_id = new_id("external_resource")
    agent_id = new_id("agent")
    artifact_id = new_id("artifact")
    provider_resource_id = new_id("external_resource")
    recorded_at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    first = repositories.RepositoryRunProvenance(
        run_id=run_id,
        task_id=task_id,
        repository_id=repository_id,
        input_revision=_INPUT_REVISION,
        branch_ref="main",
        actor_ref="user:repository-user",
        agent_id=agent_id,
        provider_resource_ids=(provider_resource_id,),
        recorded_at=recorded_at,
    )
    store = repositories.SqliteRepositoryProvenanceStore(path)
    store.record(first)
    store.record(first)

    restarted = repositories.SqliteRepositoryProvenanceStore(path)
    assert restarted.get(run_id, repository_id) == first
    assert restarted.for_run(run_id) == (first,)

    updated = repositories.RepositoryRunProvenance(
        run_id=run_id,
        task_id=task_id,
        repository_id=repository_id,
        input_revision=_INPUT_REVISION,
        branch_ref="main",
        output_revision=_OUTPUT_REVISION,
        actor_ref="user:repository-user",
        agent_id=agent_id,
        diff_artifact_ids=(artifact_id,),
        provider_resource_ids=(provider_resource_id,),
        recorded_at=datetime(2026, 9, 5, 12, 5, tzinfo=UTC),
    )
    restarted.upsert(updated)

    second_restart = repositories.SqliteRepositoryProvenanceStore(path)
    assert second_restart.get(run_id, repository_id) == updated
    assert second_restart.for_run(run_id) == (updated,)
