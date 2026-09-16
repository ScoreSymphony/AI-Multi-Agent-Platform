"""Concrete single-host process backend for canonical Application lifecycle contracts."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

from ai_multi_agent_platform.configuration import SecretAccessContext
from ai_multi_agent_platform.security import SecretReference, redact_text

from .models import (
    ApplicationDesiredState,
    ApplicationEndpoint,
    ApplicationEndpointProtocol,
    ApplicationEndpointResolution,
    ApplicationHealthCheckKind,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLogEntry,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationServiceState,
    ApplicationVolumeBinding,
)
from .runtime import (
    ApplicationInstanceNotFoundError,
    ApplicationPreparationError,
    ApplicationRuntimeDescriptor,
    ApplicationRuntimeError,
    ApplicationRuntimeUnavailableError,
)

type SecretMaterialResolver = Callable[[SecretReference, SecretAccessContext], str]
type VolumePathResolver = Callable[[ApplicationVolumeBinding], Path]

_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "VIRTUAL_ENV",
)


class LocalProcessApplicationRuntime:
    """Reference backend for long-running local processes.

    The backend never uses a shell. Canonical volume bindings are resolved by an injected
    platform-owned resolver and exposed as ``APPLICATION_VOLUME_<NAME>`` environment values.
    Logical endpoints receive runtime-selected loopback ports via environment variables, keeping
    host ports out of canonical identity. Process handles remain runtime-private.

    A fresh runtime object intentionally does not adopt unknown live PIDs after a platform restart;
    recovery reports a failed observation rather than risking PID-reuse or duplicate-process bugs.
    """

    def __init__(
        self,
        runtime_root: Path,
        *,
        secret_resolver: SecretMaterialResolver | None = None,
        volume_resolver: VolumePathResolver | None = None,
        stop_timeout_seconds: float = 5.0,
        max_log_entries: int = 1000,
    ) -> None:
        if stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be > 0")
        if max_log_entries < 1:
            raise ValueError("max_log_entries must be >= 1")
        self._root = runtime_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._secret_resolver = secret_resolver
        self._volume_resolver = volume_resolver
        self._stop_timeout_seconds = stop_timeout_seconds
        self._max_log_entries = max_log_entries
        self._processes: dict[tuple[str, str], subprocess.Popen[str]] = {}
        self._ports: dict[tuple[str, str, str], int] = {}
        self._logs: dict[str, deque[ApplicationLogEntry]] = {}
        self._lock = threading.Lock()

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor:
        return ApplicationRuntimeDescriptor(
            runtime_id="local-process",
            supported_service_runtimes=frozenset({ApplicationServiceRuntime.PROCESS}),
            capabilities=frozenset(
                {
                    "dynamic-loopback-endpoints",
                    "explicit-argv",
                    "secret-env",
                    "volume-path-env",
                }
            ),
        )

    def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        self._validate_manifest(request)
        instance = ApplicationInstance(
            application_id=request.manifest.application_id,
            application_version=request.manifest.version,
            runtime_id=self.descriptor.runtime_id,
            desired_state=ApplicationDesiredState.STOPPED,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
            configuration=request.resolved_configuration(),
            secret_bindings=request.secret_bindings,
            volume_bindings=request.volume_bindings,
            service_states=tuple(
                ApplicationServiceState(
                    service_id=service.service_id,
                    observed_state=ApplicationObservedState.STOPPED,
                    health=ApplicationHealthStatus.UNKNOWN,
                )
                for service in request.manifest.services
            ),
        )
        self._instance_root(instance.instance_id).mkdir(parents=True, exist_ok=False)
        return instance

    def start(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        self._require_instance_root(instance.instance_id)
        started: list[ApplicationService] = []
        try:
            for service in _dependency_order(manifest):
                key = (instance.instance_id, service.service_id)
                current = self._processes.get(key)
                if current is not None and current.poll() is None:
                    started.append(service)
                    continue
                environment, sensitive_values = self._environment(manifest, instance, service)
                service_root = self._service_root(instance.instance_id, service.service_id)
                service_root.mkdir(parents=True, exist_ok=True)
                command = (*service.process, *service.command)
                process = subprocess.Popen(
                    command,
                    cwd=service_root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self._processes[key] = process
                self._capture_logs(instance.instance_id, service.service_id, process, sensitive_values)
                started.append(service)
        except (OSError, ValueError) as exc:
            for service in reversed(started):
                self._stop_service(instance.instance_id, service.service_id)
            raise ApplicationRuntimeError(f"failed to start application process: {exc}") from exc
        return self.status(manifest, instance)

    def stop(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        self._require_instance_root(instance.instance_id)
        for service in reversed(_dependency_order(manifest)):
            self._stop_service(instance.instance_id, service.service_id)
        self._clear_ports(instance.instance_id)
        return replace(
            instance,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
            service_states=tuple(
                ApplicationServiceState(
                    service_id=service.service_id,
                    observed_state=ApplicationObservedState.STOPPED,
                    health=ApplicationHealthStatus.UNKNOWN,
                )
                for service in manifest.services
            ),
            endpoints=(),
        )

    def restart(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        stopped = self.stop(manifest, instance)
        return self.start(manifest, stopped)

    def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        root = self._instance_root(instance.instance_id)
        if root.exists():
            self.stop(manifest, instance)
            shutil.rmtree(root)
        with self._lock:
            self._logs.pop(instance.instance_id, None)
        return replace(
            instance,
            observed_state=ApplicationObservedState.REMOVED,
            health=ApplicationHealthStatus.UNKNOWN,
            service_states=tuple(
                ApplicationServiceState(
                    service_id=service.service_id,
                    observed_state=ApplicationObservedState.REMOVED,
                    health=ApplicationHealthStatus.UNKNOWN,
                )
                for service in manifest.services
            ),
            endpoints=(),
        )

    def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        self._require_instance_root(instance.instance_id)
        states: list[ApplicationServiceState] = []
        any_failed = False
        all_running = True
        for service in manifest.services:
            process = self._processes.get((instance.instance_id, service.service_id))
            if process is None:
                all_running = False
                state = (
                    ApplicationObservedState.STOPPED
                    if instance.desired_state is ApplicationDesiredState.STOPPED
                    else ApplicationObservedState.FAILED
                )
                any_failed = any_failed or state is ApplicationObservedState.FAILED
                health = ApplicationHealthStatus.UNKNOWN
            elif process.poll() is None:
                state = ApplicationObservedState.RUNNING
                health = self._service_health(manifest, instance, service)
            else:
                all_running = False
                any_failed = True
                state = ApplicationObservedState.FAILED
                health = ApplicationHealthStatus.UNHEALTHY
            states.append(
                ApplicationServiceState(
                    service_id=service.service_id,
                    observed_state=state,
                    health=health,
                )
            )
        observed = ApplicationObservedState.RUNNING if all_running else ApplicationObservedState.STOPPED
        if any_failed:
            observed = ApplicationObservedState.FAILED
        health = _aggregate_health(states)
        return replace(
            instance,
            observed_state=observed,
            health=health,
            service_states=tuple(states),
            endpoints=self.endpoints(manifest, instance) if all_running else (),
        )

    def health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationHealthStatus:
        return self.status(manifest, instance).health

    def endpoints(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> tuple[ApplicationEndpointResolution, ...]:
        resolved: list[ApplicationEndpointResolution] = []
        for service in manifest.services:
            process = self._processes.get((instance.instance_id, service.service_id))
            if process is None or process.poll() is not None:
                continue
            for endpoint in service.endpoints:
                port = self._ports.get((instance.instance_id, service.service_id, endpoint.name))
                if port is None:
                    continue
                resolved.append(_endpoint_resolution(service, endpoint, port))
        return tuple(resolved)

    def logs(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._require_instance_root(instance.instance_id)
        with self._lock:
            entries = tuple(self._logs.get(instance.instance_id, ()))
        if service_id is not None:
            entries = tuple(entry for entry in entries if entry.service_id == service_id)
        return entries[-limit:]

    def reconcile(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        if instance.desired_state is ApplicationDesiredState.RUNNING:
            if not self._has_any_process_handle(instance.instance_id):
                raise ApplicationRuntimeUnavailableError(
                    "local process runtime cannot safely re-adopt unknown processes after restart"
                )
            return self.start(manifest, instance)
        if instance.desired_state is ApplicationDesiredState.REMOVED:
            return self.remove(manifest, instance)
        return self.stop(manifest, instance)

    def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        if instance.desired_state is ApplicationDesiredState.RUNNING and not self._has_any_process_handle(
            instance.instance_id
        ):
            return replace(
                instance,
                observed_state=ApplicationObservedState.FAILED,
                health=ApplicationHealthStatus.UNHEALTHY,
                service_states=tuple(
                    ApplicationServiceState(
                        service_id=service.service_id,
                        observed_state=ApplicationObservedState.FAILED,
                        health=ApplicationHealthStatus.UNHEALTHY,
                        message="process handle unavailable after runtime restart",
                    )
                    for service in manifest.services
                ),
                endpoints=(),
            )
        return self.status(manifest, instance)

    def _validate_manifest(self, request: ApplicationInstallRequest) -> None:
        if any(
            service.runtime is not ApplicationServiceRuntime.PROCESS
            for service in request.manifest.services
        ):
            raise ApplicationPreparationError("local process runtime supports process services only")
        if request.secret_bindings and self._secret_resolver is None:
            raise ApplicationPreparationError(
                "application secret bindings require a configured secret material resolver"
            )
        if request.volume_bindings and self._volume_resolver is None:
            raise ApplicationPreparationError(
                "application volume bindings require a configured platform volume resolver"
            )

    def _environment(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        service: ApplicationService,
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        environment = {name: value for name in _ENV_ALLOWLIST if (value := os.environ.get(name))}
        for field in manifest.configuration:
            if field.environment_variable is None:
                continue
            value = instance.configuration.get(field.name)
            if value is not None:
                environment[field.environment_variable] = _environment_value(value)
        sensitive_values: list[str] = []
        for field in manifest.secrets:
            reference = instance.secret_bindings.get(field.name)
            if reference is None or field.environment_variable is None:
                continue
            if self._secret_resolver is None:
                raise ApplicationRuntimeUnavailableError("secret material resolver is unavailable")
            value = self._secret_resolver(
                reference,
                SecretAccessContext(
                    consumer_ref=f"application:{instance.instance_id}",
                    action="application.runtime.start",
                    purpose=f"application:{manifest.application_id}:{field.name}",
                ),
            )
            environment[field.environment_variable] = value
            sensitive_values.append(value)
        for binding in instance.volume_bindings:
            if self._volume_resolver is None:
                raise ApplicationRuntimeUnavailableError("volume path resolver is unavailable")
            source = self._volume_resolver(binding).resolve()
            if not source.exists():
                raise ApplicationRuntimeUnavailableError(
                    f"resolved application volume is unavailable: {binding.volume_name}"
                )
            environment[f"APPLICATION_VOLUME_{_environment_token(binding.volume_name)}"] = str(source)
        for endpoint in service.endpoints:
            key = (instance.instance_id, service.service_id, endpoint.name)
            port = self._ports.get(key)
            if port is None:
                port = _allocate_loopback_port()
                self._ports[key] = port
            prefix = (
                f"APPLICATION_ENDPOINT_{_environment_token(service.service_id)}_"
                f"{_environment_token(endpoint.name)}"
            )
            environment[f"{prefix}_HOST"] = "127.0.0.1"
            environment[f"{prefix}_PORT"] = str(port)
            environment[f"{prefix}_TARGET_PORT"] = str(endpoint.target_port)
        return environment, tuple(sensitive_values)

    def _service_health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        service: ApplicationService,
    ) -> ApplicationHealthStatus:
        check = service.health_check
        if check is None:
            return ApplicationHealthStatus.HEALTHY
        if check.kind is ApplicationHealthCheckKind.ENDPOINT:
            endpoint = next(
                item for item in service.endpoints if item.name == check.endpoint_name
            )
            port = self._ports.get((instance.instance_id, service.service_id, endpoint.name))
            if port is None:
                return ApplicationHealthStatus.UNHEALTHY
            return _endpoint_health(endpoint, port, check.timeout_seconds)
        try:
            result = subprocess.run(
                check.command,
                cwd=self._service_root(instance.instance_id, service.service_id),
                env={name: value for name in _ENV_ALLOWLIST if (value := os.environ.get(name))},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=check.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ApplicationHealthStatus.UNHEALTHY
        return (
            ApplicationHealthStatus.HEALTHY
            if result.returncode == 0
            else ApplicationHealthStatus.UNHEALTHY
        )

    def _capture_logs(
        self,
        instance_id: str,
        service_id: str,
        process: subprocess.Popen[str],
        sensitive_values: tuple[str, ...],
    ) -> None:
        if process.stdout is None:
            return

        def drain() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                message = redact_text(line.rstrip("\r\n"), sensitive_values)
                if not message:
                    continue
                entry = ApplicationLogEntry(message=message, service_id=service_id)
                with self._lock:
                    self._logs.setdefault(
                        instance_id,
                        deque(maxlen=self._max_log_entries),
                    ).append(entry)

        threading.Thread(target=drain, daemon=True).start()

    def _stop_service(self, instance_id: str, service_id: str) -> None:
        process = self._processes.pop((instance_id, service_id), None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=self._stop_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self._stop_timeout_seconds)

    def _clear_ports(self, instance_id: str) -> None:
        self._ports = {key: value for key, value in self._ports.items() if key[0] != instance_id}

    def _has_any_process_handle(self, instance_id: str) -> bool:
        return any(key[0] == instance_id for key in self._processes)

    def _instance_root(self, instance_id: str) -> Path:
        return self._root / instance_id

    def _require_instance_root(self, instance_id: str) -> Path:
        root = self._instance_root(instance_id)
        if not root.is_dir():
            raise ApplicationInstanceNotFoundError(
                f"local process runtime instance is not prepared: {instance_id}"
            )
        return root

    def _service_root(self, instance_id: str, service_id: str) -> Path:
        return self._instance_root(instance_id) / service_id


def _dependency_order(manifest: ApplicationManifest) -> tuple[ApplicationService, ...]:
    services = {service.service_id: service for service in manifest.services}
    ordered: list[ApplicationService] = []
    visited: set[str] = set()

    def visit(service: ApplicationService) -> None:
        if service.service_id in visited:
            return
        for dependency in service.depends_on:
            visit(services[dependency])
        visited.add(service.service_id)
        ordered.append(service)

    for service in manifest.services:
        visit(service)
    return tuple(ordered)


def _aggregate_health(states: list[ApplicationServiceState]) -> ApplicationHealthStatus:
    health = {state.health for state in states}
    if health == {ApplicationHealthStatus.HEALTHY}:
        return ApplicationHealthStatus.HEALTHY
    if ApplicationHealthStatus.UNHEALTHY in health:
        return ApplicationHealthStatus.UNHEALTHY
    if ApplicationHealthStatus.DEGRADED in health:
        return ApplicationHealthStatus.DEGRADED
    return ApplicationHealthStatus.UNKNOWN


def _endpoint_resolution(
    service: ApplicationService,
    endpoint: ApplicationEndpoint,
    port: int,
) -> ApplicationEndpointResolution:
    scheme = endpoint.protocol.value
    path = endpoint.path or ""
    return ApplicationEndpointResolution(
        endpoint_ref=f"{service.service_id}.{endpoint.name}",
        uri=f"{scheme}://127.0.0.1:{port}{path}",
        exposure=endpoint.exposure,
    )


def _endpoint_health(
    endpoint: ApplicationEndpoint,
    port: int,
    timeout_seconds: float,
) -> ApplicationHealthStatus:
    try:
        if endpoint.protocol is ApplicationEndpointProtocol.TCP:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout_seconds):
                pass
        else:
            with urlopen(
                _endpoint_resolution(
                    ApplicationService(
                        service_id="health",
                        runtime=ApplicationServiceRuntime.PROCESS,
                        process=("health",),
                    ),
                    endpoint,
                    port,
                ).uri,
                timeout=timeout_seconds,
            ) as response:
                if response.status >= 500:
                    return ApplicationHealthStatus.UNHEALTHY
    except (OSError, ValueError):
        return ApplicationHealthStatus.UNHEALTHY
    return ApplicationHealthStatus.HEALTHY


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _environment_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _environment_token(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).upper()
