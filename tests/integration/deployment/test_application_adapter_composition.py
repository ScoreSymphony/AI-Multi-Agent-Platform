from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ai_multi_agent_platform.adapters.application_runtime import ApplicationRuntimeComposition
from ai_multi_agent_platform.adapters.single_node_app import (
    build_default_single_node_deployment,
    main as server_main,
)
from ai_multi_agent_platform.applications import (
    ApplicationInstallRequest,
    ApplicationManifest,
    ApplicationService,
    ApplicationServiceRuntime,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, load_startup_recovery_report
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.upgrade.versioning import (
    JsonVersionStateStore,
    current_release_versions,
)


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Production composition fixture",
        version="1.0.0",
        description="Managed Application composition fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(sys.executable, "-c", "print('ready')"),
            ),
        ),
        runtime_requirements=("local", "process"),
    )


def _application_runtime(deployment: object) -> ApplicationRuntimeComposition:
    extensions = getattr(deployment, "startup_recovery_extensions")
    applications = [
        extension
        for extension in extensions
        if isinstance(extension, ApplicationRuntimeComposition)
    ]
    assert len(applications) == 1
    return applications[0]


def test_shipped_single_node_composes_application_runtime_and_control_plane(tmp_path: Path) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )
    applications = _application_runtime(deployment)

    assert applications.runtimes.list_runtime_ids() == ("local.process",)
    descriptor = applications.runtimes.get("local.process").descriptor
    assert "local" in descriptor.capabilities
    assert "process" in descriptor.capabilities
    assert "workspace_cwd" in descriptor.capabilities
    assert "applications" in deployment.control_plane.registered_collections
    assert "application-instances" in deployment.control_plane.registered_collections
    assert "application.install" in deployment.control_plane.registered_commands
    assert deployment.control_plane.resource_owner("applications") == "applications"
    assert deployment.control_plane.command_owner("application.start") == "applications"


def test_server_startup_recovery_reconciles_durable_applications(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "restart"
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=root, secure_cookie=False)
    )
    applications = _application_runtime(deployment)
    installed = asyncio.run(
        applications.lifecycle.install(
            ApplicationInstallRequest(manifest=_manifest()),
            runtime_id="local.process",
        )
    )
    assert installed.desired_state.value == "stopped"

    JsonVersionStateStore.for_data_dir(root).write(current_release_versions())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["recover-startup"]) == 0

    report = load_startup_recovery_report(root)
    assert report is not None
    assert report["ready_for_service"] is True
    assert report["extension_items_checked"] == 1
    assert report["extension_failures"] == 0
    assert report["extensions"] == [
        {
            "name": "applications",
            "items_checked": 1,
            "failure_count": 0,
            "ready_for_service": True,
            "failures": [],
        }
    ]

    restarted = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=root, secure_cookie=False)
    )
    recovered = _application_runtime(restarted).repository.get_instance(installed.instance_id)
    assert recovered.desired_state.value == "stopped"
    assert recovered.observed_state.value == "stopped"
