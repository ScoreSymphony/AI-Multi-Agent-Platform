from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


_INPUT_REVISION = "1" * 40
_OUTPUT_REVISION = "2" * 40


def _id(kind: str) -> str:
    return f"{kind}_{uuid4()}"


def test_sqlite_repository_provenance_survives_restart_and_upsert(tmp_path: Path) -> None:
    from ai_multi_agent_platform.repositories import (
        RepositoryRunProvenance,
        SqliteRepositoryProvenanceStore,
    )

    path = tmp_path / "repository-provenance.sqlite3"
    run_id = _id("run")
    task_id = _id("task")
    repository_id = _id("external_resource")
    agent_id = _id("agent")
    artifact_id = _id("artifact")
    provider_resource_id = _id("external_resource")
    recorded_at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    first = RepositoryRunProvenance(
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
    store = SqliteRepositoryProvenanceStore(path)
    store.record(first)
    store.record(first)

    restarted = SqliteRepositoryProvenanceStore(path)
    assert restarted.get(run_id, repository_id) == first
    assert restarted.for_run(run_id) == (first,)

    updated = RepositoryRunProvenance(
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

    second_restart = SqliteRepositoryProvenanceStore(path)
    assert second_restart.get(run_id, repository_id) == updated
    assert second_restart.for_run(run_id) == (updated,)
