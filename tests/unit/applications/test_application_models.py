from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.applications.definition import Application
from ai_multi_agent_platform.applications.models import (
    ApplicationConfigurationField,
    ApplicationConfigValueType,
    ApplicationEndpoint,
    ApplicationEndpointExposure,
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationInstallRequest,
    ApplicationManifest,
    ApplicationMaturity,
    ApplicationSecretField,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationUi,
    ApplicationVolumeBinding,
    ApplicationVolumeKind,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Example app",
        version="1.0.0",
        description="Example managed application",
        services=(
            ApplicationService(
                service_id="web",
                runtime=ApplicationServiceRuntime.OCI_IMAGE,
                image="example/web:1.0",
                endpoints=(
                    ApplicationEndpoint(
                        name="web",
                        target_port=8080,
                        exposure=ApplicationEndpointExposure.USER,
                    ),
                ),
                health_check=ApplicationHealthCheck(
                    kind=ApplicationHealthCheckKind.ENDPOINT,
                    endpoint_name="web",
                ),
            ),
        ),
        configuration=(
            ApplicationConfigurationField(
                name="workers",
                value_type=ApplicationConfigValueType.INTEGER,
                default=2,
                environment_variable="APP_WORKERS",
            ),
        ),
        secrets=(
            ApplicationSecretField(
                name="api_token",
                environment_variable="APP_API_TOKEN",
            ),
        ),
        ui=ApplicationUi(endpoint_ref="web.web"),
        maturity=ApplicationMaturity.BETA,
    )


def test_application_wraps_manifest_with_runtime_identity_without_private_runtime_ids() -> None:
    manifest = _manifest()
    application = Application(
        manifest=manifest,
        runtime_id="compose.reference",
        source_ref="registry://applications/example",
        provenance={"registry": "local"},
        installed_at=datetime(2026, 9, 16, tzinfo=UTC),
    )

    assert application.application_id == manifest.application_id
    assert application.version == "1.0.0"
    assert application.name == "Example app"
    assert application.runtime_id == "compose.reference"
    assert dict(application.provenance) == {"registry": "local"}


def test_manifest_rejects_dependency_cycles() -> None:
    with pytest.raises(ValueError, match="dependencies must be acyclic"):
        ApplicationManifest(
            application_id=new_id("application"),
            name="Cycle",
            version="1.0",
            description="Invalid cyclic stack",
            services=(
                ApplicationService(
                    service_id="api",
                    runtime=ApplicationServiceRuntime.PROCESS,
                    process=("python", "api.py"),
                    depends_on=("db",),
                ),
                ApplicationService(
                    service_id="db",
                    runtime=ApplicationServiceRuntime.PROCESS,
                    process=("python", "db.py"),
                    depends_on=("api",),
                ),
            ),
        )


def test_install_request_requires_secret_references_and_resolves_defaults() -> None:
    manifest = _manifest()
    request = ApplicationInstallRequest(
        manifest=manifest,
        configuration={},
        secret_bindings={
            "api_token": SecretReference(
                provider="platform",
                secret_id="secret/example",
                scope="application",
            )
        },
    )

    assert dict(request.resolved_configuration()) == {"workers": 2}
    assert request.secret_bindings["api_token"].secret_id == "secret/example"


def test_install_request_rejects_plaintext_secret_bindings() -> None:
    manifest = _manifest()

    with pytest.raises(ValueError, match="must be a SecretReference"):
        ApplicationInstallRequest(
            manifest=manifest,
            secret_bindings={"api_token": "plaintext"},  # type: ignore[dict-item]
        )


def test_persistent_volume_binding_rejects_host_filesystem_path() -> None:
    with pytest.raises(ValueError, match="platform-managed reference"):
        ApplicationVolumeBinding(
            volume_name="data",
            kind=ApplicationVolumeKind.PERSISTENT,
            source_ref="/var/lib/example",
        )
