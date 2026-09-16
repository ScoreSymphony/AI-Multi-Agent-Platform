from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.applications import (
    Application,
    ApplicationDesiredState,
    ApplicationHealthStatus,
    ApplicationInstance,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationSecretField,
    ApplicationService,
    ApplicationServiceRuntime,
    SqliteApplicationRepository,
)
from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference


def _state() -> tuple[Application, ApplicationInstance]:
    application_id = new_id("application")
    manifest = ApplicationManifest(
        application_id=application_id,
        name="Durable app",
        version="1.0.0",
        description="Persistence fixture",
        services=(
            ApplicationService(
                service_id="worker",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-V"),
            ),
        ),
        secrets=(
            ApplicationSecretField(
                name="token",
                environment_variable="APP_TOKEN",
            ),
        ),
    )
    application = Application(
        manifest=manifest,
        runtime_id="local-process",
        source_ref="registry://applications/durable-app",
        provenance={"registry": "local"},
        installed_at=datetime(2026, 9, 16, 20, 0, tzinfo=UTC),
    )
    instance = ApplicationInstance(
        application_id=application_id,
        application_version="1.0.0",
        runtime_id="local-process",
        desired_state=ApplicationDesiredState.RUNNING,
        observed_state=ApplicationObservedState.FAILED,
        health=ApplicationHealthStatus.UNHEALTHY,
        instance_id=new_id("application_instance"),
        secret_bindings={
            "token": SecretReference(
                provider="platform",
                secret_id="secret/durable-app",
                scope="application",
            )
        },
        revision=3,
        created_at=datetime(2026, 9, 16, 20, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 16, 20, 2, tzinfo=UTC),
    )
    return application, instance


def test_sqlite_repository_round_trips_canonical_state_after_reopen(tmp_path) -> None:
    application, instance = _state()
    database = tmp_path / "applications.sqlite3"

    first = SqliteApplicationRepository(database)
    first.save_application(application)
    first.save_instance(instance)

    reopened = SqliteApplicationRepository(database)

    assert reopened.schema_version == 1
    assert reopened.get_application(application.application_id, application.version) == application
    assert reopened.get_instance(instance.instance_id) == instance
    assert reopened.list_instances(application_id=application.application_id) == (instance,)


def test_sqlite_repository_rejects_same_revision_mutation(tmp_path) -> None:
    application, instance = _state()
    repository = SqliteApplicationRepository(tmp_path / "applications.sqlite3")
    repository.save_application(application)
    repository.save_instance(instance)

    mutated = replace(instance, health=ApplicationHealthStatus.HEALTHY)

    with pytest.raises(ContractError, match="require a new revision"):
        repository.save_instance(mutated)


def test_sqlite_repository_rejects_application_definition_rebinding(tmp_path) -> None:
    application, _ = _state()
    repository = SqliteApplicationRepository(tmp_path / "applications.sqlite3")
    repository.save_application(application)

    with pytest.raises(ContractError, match="immutable"):
        repository.save_application(replace(application, runtime_id="another-runtime"))
