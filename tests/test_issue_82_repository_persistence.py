from datetime import UTC, datetime


_INPUT_REVISION = "1" * 40
_OUTPUT_REVISION = "2" * 40
_RUN_ID = "run_00000000-0000-4000-8000-000000000001"
_TASK_ID = "task_00000000-0000-4000-8000-000000000002"
_REPOSITORY_ID = "external_resource_00000000-0000-4000-8000-000000000003"
_AGENT_ID = "agent_00000000-0000-4000-8000-000000000004"
_ARTIFACT_ID = "artifact_00000000-0000-4000-8000-000000000005"
_PROVIDER_RESOURCE_ID = "external_resource_00000000-0000-4000-8000-000000000006"


def test_sqlite_repository_provenance_survives_restart_and_upsert(tmp_path) -> None:
    from ai_multi_agent_platform.repositories import (
        RepositoryRunProvenance,
        SqliteRepositoryProvenanceStore,
    )

    path = tmp_path / "repository-provenance.sqlite3"
    recorded_at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    first = RepositoryRunProvenance(
        run_id=_RUN_ID,
        task_id=_TASK_ID,
        repository_id=_REPOSITORY_ID,
        input_revision=_INPUT_REVISION,
        branch_ref="main",
        actor_ref="user:repository-user",
        agent_id=_AGENT_ID,
        provider_resource_ids=(_PROVIDER_RESOURCE_ID,),
        recorded_at=recorded_at,
    )
    store = SqliteRepositoryProvenanceStore(path)
    store.record(first)
    store.record(first)

    restarted = SqliteRepositoryProvenanceStore(path)
    assert restarted.get(_RUN_ID, _REPOSITORY_ID) == first
    assert restarted.for_run(_RUN_ID) == (first,)

    updated = RepositoryRunProvenance(
        run_id=_RUN_ID,
        task_id=_TASK_ID,
        repository_id=_REPOSITORY_ID,
        input_revision=_INPUT_REVISION,
        branch_ref="main",
        output_revision=_OUTPUT_REVISION,
        actor_ref="user:repository-user",
        agent_id=_AGENT_ID,
        diff_artifact_ids=(_ARTIFACT_ID,),
        provider_resource_ids=(_PROVIDER_RESOURCE_ID,),
        recorded_at=datetime(2026, 9, 5, 12, 5, tzinfo=UTC),
    )
    restarted.upsert(updated)

    second_restart = SqliteRepositoryProvenanceStore(path)
    assert second_restart.get(_RUN_ID, _REPOSITORY_ID) == updated
    assert second_restart.for_run(_RUN_ID) == (updated,)
