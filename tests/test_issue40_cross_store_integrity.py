from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.automation import (
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.backup import create_single_node_backup, restore_single_node_backup
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.onboarding import JsonModelProviderSetupStore, ModelProviderSetupRecord
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.verification import VerificationPolicy, VerificationScope
from ai_multi_agent_platform.workspaces import WorkspaceType

PASSWORD = "correct horse battery staple"


def _deployment(tmp_path: Path, name: str):
    config = SingleNodeConfig(data_dir=tmp_path / name, secure_cookie=False)
    return config, build_single_node_deployment(config)


def _restore(config: SingleNodeConfig, tmp_path: Path, name: str) -> Path:
    backup = create_single_node_backup(
        data_dir=config.data_dir,
        destination=tmp_path / f"{name}-backup",
        platform_version=__version__,
        platform_commit="issue-40-cross-store",
        quiesced=True,
    )
    return restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=tmp_path / f"{name}-restored",
        expected_platform_version=__version__,
        expected_platform_commit="issue-40-cross-store",
    )


def _recover(restored_root: Path, monkeypatch) -> int:
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")
    return server_main(["recover-restore"])


def test_restore_blocks_workspace_snapshot_missing_file_reference(tmp_path: Path, monkeypatch) -> None:
    async def prepare() -> tuple[SingleNodeConfig, str]:
        config, deployment = _deployment(tmp_path, "workspace-source")
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        project = deployment.scopes.create_project(
            key="workspace-project",
            name="Workspace project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        context = DataAccessContext(
            operation=OperationContext(
                correlation_id="issue40-workspace",
                owner_type="user",
                owner_id=admin.user_id,
                project_id=project.id,
            ),
            actor_ref=admin.user_id,
        )
        workspace = await deployment.workspaces.create_workspace(
            project_id=project.id,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
        )
        assert workspace.base_snapshot_id is not None
        return config, workspace.base_snapshot_id

    config, snapshot_id = asyncio.run(prepare())
    restored_root = _restore(config, tmp_path, "workspace")
    missing_file_id = new_id("file")
    missing_hash = "0" * 64
    files = [
        {
            "relative_path": "missing.txt",
            "file_id": missing_file_id,
            "sha256": missing_hash,
        }
    ]
    checksum = hashlib.sha256(
        f"missing.txt\0{missing_file_id}\0{missing_hash}\n".encode("ascii")
    ).hexdigest()
    with sqlite3.connect(restored_root / "db" / "workspaces.sqlite3") as connection:
        connection.execute(
            "UPDATE workspace_snapshots SET files_json = ?, content_checksum = ? "
            "WHERE snapshot_id = ?",
            (json.dumps(files), checksum, snapshot_id),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_authorization_policy_missing_project(tmp_path: Path, monkeypatch) -> None:
    config, deployment = _deployment(tmp_path, "authorization-source")
    admin = deployment.bootstrap_admin("admin", PASSWORD)
    restored_root = _restore(config, tmp_path, "authorization")
    missing_project = new_id("project")
    with sqlite3.connect(restored_root / "db" / "authorization.sqlite3") as connection:
        connection.execute(
            "UPDATE authorization_policies SET project_ids_json = ? WHERE principal_ref = ?",
            (json.dumps([missing_project]), admin.user_id),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_automation_missing_project_reference(tmp_path: Path, monkeypatch) -> None:
    async def prepare() -> SingleNodeConfig:
        config, deployment = _deployment(tmp_path, "automation-source")
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        project = deployment.scopes.create_project(
            key="automation-project",
            name="Automation project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.control_plane.automation_service.create_automation(
            name="Restore me",
            description="Cross-store validation fixture",
            identity=IdentityContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
            ),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=TaskTemplate(title="Generated task", objective="Restore validation"),
            project_id=project.id,
        )
        return config

    config = asyncio.run(prepare())
    restored_root = _restore(config, tmp_path, "automation")
    with sqlite3.connect(restored_root / "db" / "automation.sqlite3") as connection:
        row = connection.execute("SELECT id, payload FROM automations LIMIT 1").fetchone()
        assert row is not None
        payload = json.loads(str(row[1]))
        payload["project_id"] = new_id("project")
        connection.execute(
            "UPDATE automations SET payload = ? WHERE id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), str(row[0])),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_verification_policy_missing_project(tmp_path: Path, monkeypatch) -> None:
    config, deployment = _deployment(tmp_path, "verification-source")
    deployment.bootstrap_admin("admin", PASSWORD)
    deployment.verification.register_policy(
        VerificationPolicy(
            name="Broken scope fixture",
            stages=(),
            scope=VerificationScope(project_ids=(new_id("project"),)),
        )
    )
    restored_root = _restore(config, tmp_path, "verification")

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_human_credential_with_missing_user(tmp_path: Path, monkeypatch) -> None:
    config, deployment = _deployment(tmp_path, "authentication-source")
    admin = deployment.bootstrap_admin("admin", PASSWORD)
    credential = deployment.authentication.create_personal_access_token(
        admin.user_id,
        purpose="issue 40 restore fixture",
    )
    restored_root = _restore(config, tmp_path, "authentication")
    with sqlite3.connect(restored_root / "db" / "authentication.sqlite3") as connection:
        connection.execute(
            "UPDATE auth_credentials SET owner_id = ? WHERE credential_id = ?",
            (new_id("user"), credential.credential_id),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_manifest_records_structured_external_dependencies(tmp_path: Path) -> None:
    config, _ = _deployment(tmp_path, "dependency-source")
    JsonModelProviderSetupStore(config.database_dir / "model-providers.json").save(
        (
            ModelProviderSetupRecord(
                provider_id="local-model-provider",
                adapter_id="local-adapter",
                base_url="http://127.0.0.1:1234",
                models={"default": "model-a"},
                credential_ref=SecretReference(
                    provider="local-secret-backend",
                    secret_id="model-provider-key",
                    scope="model-provider",
                ),
            ),
        )
    )
    backup = create_single_node_backup(
        data_dir=config.data_dir,
        destination=tmp_path / "dependency-backup",
        platform_version=__version__,
        deployment_metadata={
            "profile": "single-node",
            "optional_adapters": ["forge-sidecar"],
        },
        quiesced=True,
    )
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    dependencies = manifest["external_dependencies"]
    assert all(isinstance(item, dict) for item in dependencies)
    by_id = {item["dependency_id"]: item for item in dependencies}
    assert by_id["model-provider:local-model-provider"]["metadata"] == {
        "adapter_id": "local-adapter",
        "provider_id": "local-model-provider",
    }
    assert by_id["secret-provider:local-secret-backend"]["restore_blocking"] is False
    assert by_id["adapter-runtime:forge-sidecar"]["required"] is False
    serialized = json.dumps(dependencies, sort_keys=True)
    assert "model-provider-key" not in serialized
