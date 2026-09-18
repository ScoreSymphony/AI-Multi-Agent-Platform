from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ai_multi_agent_platform.adapters.application_runtime import ApplicationRuntimeComposition
from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.applications import (
    APPLICATION_AUDIT_COLLECTION,
    ApplicationConfigurationField,
    ApplicationConfigValueType,
    ApplicationManifest,
    ApplicationService,
    ApplicationServiceRuntime,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.control_plane import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, SingleNodeDeployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import FailureComponent, TelemetryOutcome


def _applications(deployment: SingleNodeDeployment) -> ApplicationRuntimeComposition:
    matches = [
        extension
        for extension in deployment.startup_recovery_extensions
        if isinstance(extension, ApplicationRuntimeComposition)
    ]
    assert len(matches) == 1
    return matches[0]


def _context(principal_ref: str, key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref=principal_ref,
            owner_type="user",
            owner_id=principal_ref,
        ),
        idempotency_key=key,
    )


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Audit observability fixture",
        version="1.0.0",
        description="Proves durable post-success Application audit and telemetry",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(sys.executable, "-c", "import time; time.sleep(30)"),
            ),
        ),
        configuration=(
            ApplicationConfigurationField(
                name="label",
                value_type=ApplicationConfigValueType.STRING,
                default="before",
                mutable=True,
                environment_variable="APP_LABEL",
            ),
        ),
        runtime_requirements=("local", "process"),
    )


def test_shipped_application_commands_are_durably_audited_and_observable(tmp_path: Path) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )
    account = deployment.bootstrap_admin("admin", "application-audit-password")
    applications = _applications(deployment)
    manifest = _manifest()
    instance_id: str | None = None

    async def scenario() -> None:
        nonlocal instance_id
        installed = await deployment.control_plane.execute_command(
            _context(account.user_id, "application-audit-install"),
            "application.install",
            manifest.application_id,
            {
                "manifest": application_manifest_to_document(manifest),
                "runtime_id": "local.process",
            },
        )
        instance_value = installed["id"]
        assert isinstance(instance_value, str)
        instance_id = instance_value

        configured = await deployment.control_plane.execute_command(
            _context(account.user_id, "application-audit-configure"),
            "application.configure",
            instance_id,
            {"configuration": {"label": "after"}},
        )
        assert configured["configuration"] == {"label": "after"}

        started = await deployment.control_plane.execute_command(
            _context(account.user_id, "application-audit-start"),
            "application.start",
            instance_id,
            {},
        )
        assert started["observed_state"] == "running"

        stopped = await deployment.control_plane.execute_command(
            _context(account.user_id, "application-audit-stop"),
            "application.stop",
            instance_id,
            {},
        )
        assert stopped["observed_state"] == "stopped"

        listed = await deployment.control_plane.list_extension_resources(
            _context(account.user_id, "application-audit-list"),
            APPLICATION_AUDIT_COLLECTION,
            PageQuery(filters={"application_id": manifest.application_id}),
        )
        resources = listed["items"]
        assert isinstance(resources, list)
        assert [resource["command"] for resource in resources] == [
            "application.install",
            "application.configure",
            "application.start",
            "application.stop",
        ]

    try:
        asyncio.run(scenario())
    finally:
        if instance_id is not None:
            asyncio.run(applications.lifecycle.stop(instance_id))

    persisted = applications.audit_store.list(application_id=manifest.application_id)
    assert [resource["command"] for resource in persisted] == [
        "application.install",
        "application.configure",
        "application.start",
        "application.stop",
    ]
    for resource in persisted:
        assert resource["application_id"] == manifest.application_id
        assert resource["actor_ref"] == account.user_id
        assert resource["instance_id"] == instance_id
        assert "configuration" not in resource
        assert "secret_bindings" not in resource
        assert "volume_bindings" not in resource

    reopened = applications.audit_store.__class__(
        deployment.config.database_dir / "applications.sqlite3"
    )
    assert reopened.list(application_id=manifest.application_id) == persisted

    exporter = deployment.observability_exporter
    application_logs = [
        record
        for record in exporter.logs
        if record.component is FailureComponent.APPLICATION_ADAPTER
    ]
    application_metrics = [
        record
        for record in exporter.metrics
        if record.name == "platform.application.commands.succeeded"
    ]
    application_timeline = [
        record
        for record in exporter.timeline
        if record.component is FailureComponent.APPLICATION_ADAPTER
    ]
    assert [record.event_name for record in application_logs] == [
        "application.install",
        "application.configure",
        "application.start",
        "application.stop",
    ]
    assert all(record.outcome is TelemetryOutcome.SUCCEEDED for record in application_logs)
    assert len(application_metrics) == 4
    assert [record.event_name for record in application_timeline] == [
        "application.install",
        "application.configure",
        "application.start",
        "application.stop",
    ]
