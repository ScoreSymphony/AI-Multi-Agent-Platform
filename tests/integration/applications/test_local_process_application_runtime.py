from __future__ import annotations

import sys
import time
from urllib.request import urlopen

import pytest

from ai_multi_agent_platform.applications import (
    ApplicationDesiredState,
    ApplicationEndpoint,
    ApplicationEndpointExposure,
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationLifecycleService,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationRuntimeError,
    ApplicationRuntimeRegistry,
    ApplicationService,
    ApplicationServiceRuntime,
    InMemoryApplicationRepository,
    LocalProcessApplicationRuntime,
)
from ai_multi_agent_platform.domain import new_id

_HTTP_SERVER = """
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(os.environ["APPLICATION_ENDPOINT_WEB_WEB_PORT"])

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return

print("ready", flush=True)
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _single_service_manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Local HTTP fixture",
        version="1.0.0",
        description="Single-service local process fixture",
        services=(
            ApplicationService(
                service_id="web",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(sys.executable, "-u", "-c", _HTTP_SERVER),
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
                    timeout_seconds=0.2,
                ),
            ),
        ),
    )


def test_local_process_runtime_single_service_end_to_end(tmp_path) -> None:
    runtime = LocalProcessApplicationRuntime(tmp_path / "runtime")
    repository = InMemoryApplicationRepository()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    installed = lifecycle.install(
        ApplicationInstallRequest(manifest=_single_service_manifest()),
        runtime_id="local-process",
    )

    started = lifecycle.start(installed.instance_id)
    assert started.desired_state is ApplicationDesiredState.RUNNING
    assert started.observed_state is ApplicationObservedState.RUNNING

    deadline = time.monotonic() + 3.0
    health = ApplicationHealthStatus.UNKNOWN
    while time.monotonic() < deadline:
        health = lifecycle.health(installed.instance_id)
        if health is ApplicationHealthStatus.HEALTHY:
            break
        time.sleep(0.02)
    assert health is ApplicationHealthStatus.HEALTHY

    endpoint = lifecycle.endpoints(installed.instance_id)[0]
    assert endpoint.endpoint_ref == "web.web"
    with urlopen(endpoint.uri, timeout=1.0) as response:
        assert response.read() == b"ok"

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not lifecycle.logs(installed.instance_id):
        time.sleep(0.01)
    assert any(entry.message == "ready" for entry in lifecycle.logs(installed.instance_id))

    stopped = lifecycle.stop(installed.instance_id)
    assert stopped.desired_state is ApplicationDesiredState.STOPPED
    assert stopped.observed_state is ApplicationObservedState.STOPPED
    assert lifecycle.endpoints(installed.instance_id) == ()


def test_local_process_runtime_rolls_back_partial_multi_service_start(tmp_path) -> None:
    runtime = LocalProcessApplicationRuntime(tmp_path / "runtime")
    repository = InMemoryApplicationRepository()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    manifest = ApplicationManifest(
        application_id=new_id("application"),
        name="Partial start fixture",
        version="1.0.0",
        description="Proves dependency rollback semantics",
        services=(
            ApplicationService(
                service_id="first",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(
                    sys.executable,
                    "-u",
                    "-c",
                    "import time; print('first-ready', flush=True); time.sleep(60)",
                ),
            ),
            ApplicationService(
                service_id="second",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("application-runtime-executable-that-does-not-exist",),
                depends_on=("first",),
            ),
        ),
    )
    installed = lifecycle.install(
        ApplicationInstallRequest(manifest=manifest),
        runtime_id="local-process",
    )

    with pytest.raises(ApplicationRuntimeError, match="failed to start"):
        lifecycle.start(installed.instance_id)

    persisted = repository.get_instance(installed.instance_id)
    assert persisted.desired_state is ApplicationDesiredState.RUNNING
    assert persisted.observed_state is ApplicationObservedState.FAILED
    assert persisted.health is ApplicationHealthStatus.UNHEALTHY
    assert runtime.endpoints(manifest, persisted) == ()
