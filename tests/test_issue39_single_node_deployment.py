from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from ai_multi_agent_platform.configuration import ConfigurationError
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import (
    SingleNodeConfig,
    build_single_node_deployment,
    load_single_node_config,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus

PASSWORD = "correct horse battery staple"


def test_single_node_configuration_is_explicit_and_rejects_insecure_public_cookie_mode(
    tmp_path: Path,
) -> None:
    config = load_single_node_config(
        {
            "AI_MAP_DATA_DIR": str(tmp_path / "platform"),
            "AI_MAP_HOST": "127.0.0.1",
            "AI_MAP_PORT": "8123",
            "AI_MAP_SECURE_COOKIE": "false",
            "AI_MAP_LOG_LEVEL": "debug",
            "IGNORED_SECRET": "must-not-be-imported",
        }
    )
    assert config.data_dir == tmp_path / "platform"
    assert config.port == 8123
    assert config.secure_cookie is False
    assert config.log_level == "debug"

    with pytest.raises(ConfigurationError, match="loopback-only"):
        load_single_node_config(
            {
                "AI_MAP_HOST": "0.0.0.0",
                "AI_MAP_SECURE_COOKIE": "false",
            }
        )


def test_single_node_profile_persists_auth_policy_scope_and_canonical_task_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin("admin", PASSWORD)
        login = first.authentication.login("admin", PASSWORD)
        credential = first.authentication.create_personal_access_token(
            admin.user_id,
            purpose="restart-smoke",
        )
        project = first.scopes.create_project(
            key="bootstrap-project",
            name="Deployment smoke",
            owner_type="user",
            owner_id=admin.user_id,
        )

        task = await first.kernel.create_task(
            idempotency_key="deployment-task",
            title="Single-node smoke",
            objective="Prove the durable reference execution path",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        await first.kernel.ready_task(
            idempotency_key="deployment-task-ready",
            task_id=task.task_id,
        )
        run = await first.kernel.start_task(
            idempotency_key="deployment-task-start",
            task_id=task.task_id,
        )
        refreshed = await first.kernel.refresh_run(
            idempotency_key="deployment-task-refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        assert refreshed.status is RunStatus.SUCCEEDED
        assert (await first.kernel.get_task(task.task_id)).status is TaskStatus.SUCCEEDED

        restarted = build_single_node_deployment(config)
        actor = restarted.authentication.authenticate_bearer(credential.secret)
        assert actor.identity.actor_id == admin.user_id
        session_actor = restarted.authentication.authenticate_session(
            login.session.token,
            csrf_token=login.session.csrf_token,
            require_csrf=True,
        )
        assert session_actor.identity.actor_id == admin.user_id
        assert restarted.authorization.has_policy(admin.user_id)

        same_project = restarted.scopes.create_project(
            key="bootstrap-project",
            name="ignored on idempotent retry",
            owner_type="user",
            owner_id=admin.user_id,
        )
        assert same_project.id == project.id
        persisted_task = await restarted.kernel.get_task(task.task_id)
        assert persisted_task.status is TaskStatus.SUCCEEDED

        repaired = restarted.bootstrap_admin("admin", PASSWORD)
        assert repaired.user_id == admin.user_id

        health = await restarted.http.handle(
            HTTPRequest(method="GET", path="/api/v1/health", headers={})
        )
        assert health.status == 200
        assert isinstance(health.body, dict)
        assert health.body["ready"] is True

        with sqlite3.connect(config.database_dir / "authentication.sqlite3") as connection:
            password_verifier = connection.execute(
                "SELECT password_verifier FROM auth_users WHERE user_id = ?",
                (admin.user_id,),
            ).fetchone()
            credential_verifier = connection.execute(
                "SELECT secret_verifier FROM auth_credentials WHERE credential_id = ?",
                (credential.credential_id,),
            ).fetchone()
        assert password_verifier is not None and password_verifier[0] != PASSWORD
        assert credential_verifier is not None and credential_verifier[0] != credential.secret

    asyncio.run(scenario())
