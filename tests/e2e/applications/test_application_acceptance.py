from __future__ import annotations

import asyncio
import socket
import sys
from contextlib import suppress
from pathlib import Path

from ai_multi_agent_platform.adapters.application_runtime import ApplicationRuntimeComposition
from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.applications import (
    ApplicationEndpoint,
    ApplicationEndpointExposure,
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationManifest,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationUi,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, SingleNodeDeployment
from ai_multi_agent_platform.domain import new_id

_PASSWORD = "application-acceptance-test-password"


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _applications(deployment: SingleNodeDeployment) -> ApplicationRuntimeComposition:
    matches = [
        extension
        for extension in deployment.startup_recovery_extensions
        if isinstance(extension, ApplicationRuntimeComposition)
    ]
    assert len(matches) == 1
    return matches[0]


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


async def _command(
    deployment: SingleNodeDeployment,
    token: str,
    command: str,
    resource_ref: str,
    *,
    key: str,
    payload: dict[str, object] | None = None,
):
    response = await deployment.http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/commands/{command}",
            headers=_headers(token, key=key),
            body={"resource_ref": resource_ref, **(payload or {})},
        )
    )
    return response


async def _get_instance(
    deployment: SingleNodeDeployment,
    token: str,
    instance_id: str,
):
    return await deployment.http.handle(
        HTTPRequest(
            method="GET",
            path=f"/api/v1/application-instances/{instance_id}",
            headers=_headers(token),
        )
    )


async def _cleanup_instance(
    deployment: SingleNodeDeployment,
    instance_id: str | None,
) -> None:
    if instance_id is None:
        return
    lifecycle = _applications(deployment).lifecycle
    with suppress(Exception):
        await lifecycle.stop(instance_id)


def _single_service_manifest(port: int) -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Single-service acceptance fixture",
        version="1.0.0",
        description="Proves the canonical managed Application lifecycle end to end",
        services=(
            ApplicationService(
                service_id="web",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(
                    sys.executable,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ),
                endpoints=(
                    ApplicationEndpoint(
                        name="ui",
                        target_port=port,
                        exposure=ApplicationEndpointExposure.USER,
                    ),
                ),
                health_check=ApplicationHealthCheck(
                    kind=ApplicationHealthCheckKind.ENDPOINT,
                    endpoint_name="ui",
                    interval_seconds=0.02,
                    timeout_seconds=0.2,
                    retries=30,
                ),
            ),
        ),
        ui=ApplicationUi(endpoint_ref="web.ui"),
        runtime_requirements=("local", "process"),
    )


def test_single_service_manifest_lifecycle_health_open_and_reconcile(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_default_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "single-service",
                secure_cookie=False,
            )
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="application-single-service-acceptance",
        )
        port = _unused_port()
        manifest = _single_service_manifest(port)
        instance_id: str | None = None

        try:
            installed = await _command(
                deployment,
                token.secret,
                "application.install",
                manifest.application_id,
                key="application-single-install",
                payload={
                    "manifest": application_manifest_to_document(manifest),
                    "runtime_id": "local.process",
                },
            )
            assert installed.status == 200, installed.body
            assert isinstance(installed.body, dict)
            instance_id = installed.body["id"]
            assert isinstance(instance_id, str)
            assert installed.body["desired_state"] == "stopped"
            assert installed.body["observed_state"] == "stopped"

            started = await _command(
                deployment,
                token.secret,
                "application.start",
                instance_id,
                key="application-single-start",
            )
            assert started.status == 200, started.body
            assert isinstance(started.body, dict)
            assert started.body["id"] == instance_id
            assert started.body["desired_state"] == "running"
            assert started.body["observed_state"] == "running"
            assert started.body["health"] == "healthy"
            assert started.body["open"] == {
                "endpoint_ref": "web.ui",
                "uri": f"http://127.0.0.1:{port}/",
                "open_mode": "external",
            }

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            await writer.drain()
            status_line = await asyncio.wait_for(reader.readline(), timeout=1.0)
            writer.close()
            await writer.wait_closed()
            assert status_line.startswith(b"HTTP/1.0 200")

            observed = await _get_instance(deployment, token.secret, instance_id)
            assert observed.status == 200, observed.body
            assert isinstance(observed.body, dict)
            assert observed.body["health"] == "healthy"
            assert observed.body["open"]["uri"] == f"http://127.0.0.1:{port}/"

            stopped = await _command(
                deployment,
                token.secret,
                "application.stop",
                instance_id,
                key="application-single-stop",
            )
            assert stopped.status == 200, stopped.body
            assert isinstance(stopped.body, dict)
            assert stopped.body["desired_state"] == "stopped"
            assert stopped.body["observed_state"] == "stopped"
            assert stopped.body["open"] is None

            reconciled = await _command(
                deployment,
                token.secret,
                "application.reconcile",
                instance_id,
                key="application-single-reconcile-stopped",
            )
            assert reconciled.status == 200, reconciled.body
            assert isinstance(reconciled.body, dict)
            assert reconciled.body["id"] == instance_id
            assert reconciled.body["observed_state"] == "stopped"

            restarted_from_stopped = await _command(
                deployment,
                token.secret,
                "application.start",
                instance_id,
                key="application-single-start-again",
            )
            assert restarted_from_stopped.status == 200, restarted_from_stopped.body

            restarted = await _command(
                deployment,
                token.secret,
                "application.restart",
                instance_id,
                key="application-single-restart",
            )
            assert restarted.status == 200, restarted.body
            assert isinstance(restarted.body, dict)
            assert restarted.body["id"] == instance_id
            assert restarted.body["desired_state"] == "running"
            assert restarted.body["observed_state"] == "running"
            assert restarted.body["health"] == "healthy"
            assert restarted.body["open"]["uri"] == f"http://127.0.0.1:{port}/"
        finally:
            await _cleanup_instance(deployment, instance_id)

    asyncio.run(scenario())


def _marker_service(
    *,
    service_id: str,
    marker: Path,
    lifetime_seconds: float,
    depends_on: tuple[str, ...] = (),
    health_check: ApplicationHealthCheck | None = None,
    requires_marker: Path | None = None,
) -> ApplicationService:
    prerequisite = (
        ""
        if requires_marker is None
        else f"assert Path({str(requires_marker)!r}).exists(), 'dependency not ready'; "
    )
    script = (
        "from pathlib import Path; import time; "
        + prerequisite
        + f"Path({str(marker)!r}).write_text('ready', encoding='utf-8'); "
        + f"time.sleep({lifetime_seconds!r})"
    )
    return ApplicationService(
        service_id=service_id,
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u", "-c", script),
        depends_on=depends_on,
        health_check=health_check,
    )


def _marker_health(marker: Path) -> ApplicationHealthCheck:
    command = (
        sys.executable,
        "-c",
        (f"from pathlib import Path; raise SystemExit(0 if Path({str(marker)!r}).exists() else 1)"),
    )
    return ApplicationHealthCheck(
        kind=ApplicationHealthCheckKind.COMMAND,
        command=command,
        interval_seconds=0.02,
        timeout_seconds=0.2,
        retries=30,
    )


def test_multi_service_dependency_partial_failure_and_aggregate_health(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_default_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "multi-service",
                secure_cookie=False,
            )
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="application-multi-service-acceptance",
        )
        base_marker = tmp_path / "base-ready"
        worker_marker = tmp_path / "worker-ready"
        manifest = ApplicationManifest(
            application_id=new_id("application"),
            name="Multi-service acceptance fixture",
            version="1.0.0",
            description="Proves dependency ordering and aggregate health",
            services=(
                _marker_service(
                    service_id="base",
                    marker=base_marker,
                    lifetime_seconds=30.0,
                    health_check=_marker_health(base_marker),
                ),
                _marker_service(
                    service_id="worker",
                    marker=worker_marker,
                    lifetime_seconds=0.5,
                    depends_on=("base",),
                    requires_marker=base_marker,
                ),
            ),
            runtime_requirements=("local", "process"),
        )
        instance_id: str | None = None

        try:
            installed = await _command(
                deployment,
                token.secret,
                "application.install",
                manifest.application_id,
                key="application-multi-install",
                payload={
                    "manifest": application_manifest_to_document(manifest),
                    "runtime_id": "local.process",
                },
            )
            assert installed.status == 200, installed.body
            assert isinstance(installed.body, dict)
            instance_id = installed.body["id"]
            assert isinstance(instance_id, str)

            started = await _command(
                deployment,
                token.secret,
                "application.start",
                instance_id,
                key="application-multi-start",
            )
            assert started.status == 200, started.body
            assert isinstance(started.body, dict)
            assert base_marker.exists()
            assert worker_marker.exists()
            assert started.body["observed_state"] == "running"
            assert started.body["health"] == "healthy"
            assert {
                state["service_id"]: (state["observed_state"], state["health"])
                for state in started.body["service_states"]
            } == {
                "base": ("running", "healthy"),
                "worker": ("running", "healthy"),
            }

            await asyncio.sleep(0.7)
            degraded = await _get_instance(deployment, token.secret, instance_id)
            assert degraded.status == 200, degraded.body
            assert isinstance(degraded.body, dict)
            assert degraded.body["observed_state"] == "degraded"
            assert degraded.body["health"] == "degraded"
            service_states = {
                state["service_id"]: (state["observed_state"], state["health"])
                for state in degraded.body["service_states"]
            }
            assert service_states["base"] == ("running", "healthy")
            assert service_states["worker"] == ("failed", "unhealthy")
        finally:
            await _cleanup_instance(deployment, instance_id)

        failing_base_marker = tmp_path / "partial-base-ready"
        failing_worker_marker = tmp_path / "partial-worker-ready"
        failing_manifest = ApplicationManifest(
            application_id=new_id("application"),
            name="Partial-start failure fixture",
            version="1.0.0",
            description="Proves cleanup when a dependent service cannot become healthy",
            services=(
                _marker_service(
                    service_id="base",
                    marker=failing_base_marker,
                    lifetime_seconds=30.0,
                    health_check=_marker_health(failing_base_marker),
                ),
                _marker_service(
                    service_id="worker",
                    marker=failing_worker_marker,
                    lifetime_seconds=30.0,
                    depends_on=("base",),
                    requires_marker=failing_base_marker,
                    health_check=ApplicationHealthCheck(
                        kind=ApplicationHealthCheckKind.COMMAND,
                        command=(sys.executable, "-c", "raise SystemExit(1)"),
                        interval_seconds=0.02,
                        timeout_seconds=0.2,
                        retries=2,
                    ),
                ),
            ),
            runtime_requirements=("local", "process"),
        )
        failed_instance_id: str | None = None

        try:
            installed_failure = await _command(
                deployment,
                token.secret,
                "application.install",
                failing_manifest.application_id,
                key="application-partial-install",
                payload={
                    "manifest": application_manifest_to_document(failing_manifest),
                    "runtime_id": "local.process",
                },
            )
            assert installed_failure.status == 200, installed_failure.body
            assert isinstance(installed_failure.body, dict)
            failed_instance_id = installed_failure.body["id"]
            assert isinstance(failed_instance_id, str)

            failed_start = await _command(
                deployment,
                token.secret,
                "application.start",
                failed_instance_id,
                key="application-partial-start",
            )
            assert failed_start.status == 502, failed_start.body
            assert isinstance(failed_start.body, dict)
            assert failed_start.body["code"] == "backend_error"
            assert failing_base_marker.exists()
            assert failing_worker_marker.exists()

            failed = await _get_instance(deployment, token.secret, failed_instance_id)
            assert failed.status == 200, failed.body
            assert isinstance(failed.body, dict)
            assert failed.body["desired_state"] == "running"
            assert failed.body["observed_state"] == "failed"
            assert failed.body["health"] == "unhealthy"
            assert failed.body["endpoints"] == []
            assert {state["service_id"] for state in failed.body["service_states"]} == {
                "base",
                "worker",
            }
        finally:
            await _cleanup_instance(deployment, failed_instance_id)

    asyncio.run(scenario())
