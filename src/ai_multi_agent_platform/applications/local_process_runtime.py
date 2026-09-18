"""Concrete local-process Application Runtime backend.

This backend owns real local PROCESS services and keeps process handles, resolved secret
material and Workspace host paths private to the execution boundary. Remote Node placement
and non-Workspace volume kinds continue to fail closed until explicitly wired.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from ai_multi_agent_platform.configuration import SecretAccessContext, SecretProvider
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.security import redact_text
from ai_multi_agent_platform.workspaces import MaterializationOutcome

from .local_host import (
    LocalApplicationHostProfile,
    detect_local_application_host_profile,
    local_host_resource_rejections,
)
from .models import (
    ApplicationDesiredState,
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
)
from .runtime import (
    ApplicationPreparationError,
    ApplicationRuntimeDescriptor,
    ApplicationRuntimeError,
    ApplicationRuntimeUnavailableError,
)
from .workspace_execution import (
    LocalApplicationWorkspaceBinder,
    LocalApplicationWorkspaceExecution,
)

_DEFAULT_LOG_CAPACITY = 2_000
_DEFAULT_SECRET_LIFETIME_SECONDS = 300
_SECRET_ACTION = "application.run.process"
_SECRET_CAPABILITY = "application.process"


@dataclass(slots=True)
class _ManagedProcess:
    process: asyncio.subprocess.Process
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]


class LocalProcessApplicationRuntime:
    """Reference backend for platform-managed local subprocess Applications."""

    def __init__(
        self,
        *,
        runtime_id: str = "local.process",
        stop_timeout_seconds: float = 5.0,
        log_capacity: int = _DEFAULT_LOG_CAPACITY,
        secret_provider: SecretProvider | None = None,
        secret_consumer_ref: str = "service:application-runtime",
        secret_purpose: str = "application_runtime",
        secret_lifetime_seconds: int = _DEFAULT_SECRET_LIFETIME_SECONDS,
        workspace_binder: LocalApplicationWorkspaceBinder | None = None,
        host_profile: LocalApplicationHostProfile | None = None,
        resource_probe_path: os.PathLike[str] | str | None = None,
    ) -> None:
        if stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be > 0")
        if log_capacity < 1:
            raise ValueError("log_capacity must be >= 1")
        if not secret_consumer_ref.strip():
            raise ValueError("secret_consumer_ref must not be blank")
        if not secret_purpose.strip():
            raise ValueError("secret_purpose must not be blank")
        if secret_lifetime_seconds <= 0:
            raise ValueError("secret_lifetime_seconds must be > 0")
        capabilities = {"local", "process"}
        if secret_provider is not None:
            capabilities.add("secret_environment")
        if workspace_binder is not None:
            capabilities.add("workspace_cwd")
        self._descriptor = ApplicationRuntimeDescriptor(
            runtime_id=runtime_id,
            supported_service_runtimes=frozenset({ApplicationServiceRuntime.PROCESS}),
            capabilities=frozenset(capabilities),
        )
        self._stop_timeout_seconds = stop_timeout_seconds
        self._log_capacity = log_capacity
        self._secret_provider = secret_provider
        self._secret_consumer_ref = secret_consumer_ref
        self._secret_purpose = secret_purpose
        self._secret_lifetime_seconds = secret_lifetime_seconds
        self._workspace_binder = workspace_binder
        self._host_profile = host_profile or detect_local_application_host_profile(
            disk_path=resource_probe_path
        )
        self._processes: dict[str, dict[str, _ManagedProcess]] = {}
        self._logs: dict[str, deque[ApplicationLogEntry]] = {}
        self._environments: dict[str, dict[str, str]] = {}
        self._sensitive_values: dict[str, tuple[str, ...]] = {}
        self._workspace_executions: dict[str, LocalApplicationWorkspaceExecution] = {}
        self._secret_lease_tasks: dict[str, asyncio.Task[None]] = {}
        self._expired_secret_leases: set[str] = set()

    @property
    def descriptor(self) -> ApplicationRuntimeDescriptor:
        return self._descriptor

    async def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        self._validate_request(request)
        return ApplicationInstance(
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

    async def start(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        existing = self._processes.get(instance.instance_id)
        if existing and all(item.process.returncode is None for item in existing.values()):
            return await self.status(manifest, instance)
        if existing:
            await self._stop_owned(
                manifest,
                instance.instance_id,
                workspace_outcome=MaterializationOutcome.FAILED,
                commit_workspace=False,
            )

        self._expired_secret_leases.discard(instance.instance_id)
        try:
            workspace_execution = await self._materialize_workspace(manifest, instance)
            if workspace_execution is not None:
                self._workspace_executions[instance.instance_id] = workspace_execution
            environment, sensitive_values, secret_expires_at = await self._execution_environment(
                manifest,
                instance,
                workspace_execution,
            )
            self._environments[instance.instance_id] = environment
            self._sensitive_values[instance.instance_id] = sensitive_values

            owned = self._processes.setdefault(instance.instance_id, {})
            for service in _dependency_order(manifest.services):
                managed = await self._spawn_service(manifest, instance, service)
                owned[service.service_id] = managed
                await asyncio.sleep(0)
                if managed.process.returncode is not None:
                    raise ApplicationRuntimeError(
                        f"application service exited during startup: {service.service_id} "
                        f"({managed.process.returncode})"
                    )
                if service.health_check is not None:
                    await self._wait_until_healthy(manifest, instance, service, managed)
        except asyncio.CancelledError:
            await self._finish_cleanup(
                manifest,
                instance.instance_id,
                MaterializationOutcome.CANCELLED,
            )
            raise
        except ApplicationRuntimeError:
            await self._finish_cleanup(
                manifest,
                instance.instance_id,
                MaterializationOutcome.FAILED,
            )
            raise
        except OSError as exc:
            await self._finish_cleanup(
                manifest,
                instance.instance_id,
                MaterializationOutcome.FAILED,
            )
            raise ApplicationRuntimeUnavailableError(
                f"local process runtime could not start an application service: {exc}"
            ) from exc

        if secret_expires_at is not None:
            self._secret_lease_tasks[instance.instance_id] = asyncio.create_task(
                self._expire_secret_lease(
                    manifest,
                    instance.instance_id,
                    secret_expires_at,
                )
            )
        return await self.status(manifest, instance)

    async def stop(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        await self._stop_owned(
            manifest,
            instance.instance_id,
            workspace_outcome=MaterializationOutcome.SUCCEEDED,
            commit_workspace=True,
        )
        self._expired_secret_leases.discard(instance.instance_id)
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

    async def restart(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        stopped = await self.stop(manifest, instance)
        return await self.start(manifest, stopped)

    async def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        await self._stop_owned(
            manifest,
            instance.instance_id,
            workspace_outcome=MaterializationOutcome.SUCCEEDED,
            commit_workspace=True,
        )
        self._logs.pop(instance.instance_id, None)
        self._expired_secret_leases.discard(instance.instance_id)
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

    async def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        owned = self._processes.get(instance.instance_id, {})
        service_states: list[ApplicationServiceState] = []
        live_count = 0
        unhealthy_count = 0
        for service in manifest.services:
            managed = owned.get(service.service_id)
            if managed is None:
                state = self._missing_service_state(instance, service)
            elif managed.process.returncode is not None:
                unhealthy_count += 1
                state = ApplicationServiceState(
                    service_id=service.service_id,
                    observed_state=ApplicationObservedState.FAILED,
                    health=ApplicationHealthStatus.UNHEALTHY,
                    message=f"process exited with code {managed.process.returncode}",
                )
            else:
                live_count += 1
                healthy = await self._probe_service(manifest, instance, service, managed)
                if not healthy:
                    unhealthy_count += 1
                state = ApplicationServiceState(
                    service_id=service.service_id,
                    observed_state=(
                        ApplicationObservedState.RUNNING
                        if healthy
                        else ApplicationObservedState.UNHEALTHY
                    ),
                    health=(
                        ApplicationHealthStatus.HEALTHY
                        if healthy
                        else ApplicationHealthStatus.UNHEALTHY
                    ),
                )
            service_states.append(state)

        observed_state, health = _aggregate_state(
            desired_state=instance.desired_state,
            service_count=len(manifest.services),
            live_count=live_count,
            unhealthy_count=unhealthy_count,
        )
        endpoints = await self.endpoints(manifest, instance)
        return replace(
            instance,
            observed_state=observed_state,
            health=health,
            service_states=tuple(service_states),
            endpoints=endpoints,
        )

    async def health(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationHealthStatus:
        return (await self.status(manifest, instance)).health

    async def endpoints(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> tuple[ApplicationEndpointResolution, ...]:
        owned = self._processes.get(instance.instance_id, {})
        resolved: list[ApplicationEndpointResolution] = []
        for service in manifest.services:
            managed = owned.get(service.service_id)
            if managed is None or managed.process.returncode is not None:
                continue
            for endpoint in service.endpoints:
                resolved.append(
                    ApplicationEndpointResolution(
                        endpoint_ref=f"{service.service_id}.{endpoint.name}",
                        uri=_endpoint_uri(endpoint.protocol, endpoint.target_port, endpoint.path),
                        exposure=endpoint.exposure,
                    )
                )
        return tuple(resolved)

    async def logs(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        *,
        service_id: str | None = None,
        limit: int = 200,
    ) -> tuple[ApplicationLogEntry, ...]:
        del manifest
        if limit < 1:
            raise ValueError("limit must be >= 1")
        await asyncio.sleep(0)
        entries = self._logs.get(instance.instance_id, ())
        selected = [
            entry for entry in entries if service_id is None or entry.service_id == service_id
        ]
        return tuple(selected[-limit:])

    async def reconcile(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        if instance.desired_state is ApplicationDesiredState.RUNNING:
            return await self.start(manifest, instance)
        if instance.desired_state is ApplicationDesiredState.REMOVED:
            return await self.remove(manifest, instance)
        return await self.stop(manifest, instance)

    async def recover(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        if instance.desired_state is ApplicationDesiredState.RUNNING:
            owned = self._processes.get(instance.instance_id)
            if not owned:
                raise ApplicationRuntimeUnavailableError(
                    "local process ownership was lost across runtime restart; refusing "
                    "unsafe PID-only re-adoption"
                )
            return await self.status(manifest, instance)
        if instance.desired_state is ApplicationDesiredState.REMOVED:
            return await self.remove(manifest, instance)
        return await self.stop(manifest, instance)

    def _validate_request(self, request: ApplicationInstallRequest) -> None:
        self._validate_host_requirements(request)
        unsupported = [
            service.service_id
            for service in request.manifest.services
            if service.runtime is not ApplicationServiceRuntime.PROCESS
        ]
        if unsupported:
            raise ApplicationPreparationError(
                f"local process runtime only supports PROCESS services: {unsupported!r}"
            )
        if request.node_id is not None:
            raise ApplicationPreparationError(
                "local process runtime does not accept explicit Node placement"
            )
        if request.secret_bindings:
            if self._secret_provider is None:
                raise ApplicationPreparationError(
                    "local process runtime requires a SecretProvider for secret bindings"
                )
            secret_fields = {field.name: field for field in request.manifest.secrets}
            unsupported_secrets = sorted(
                name
                for name in request.secret_bindings
                if secret_fields[name].environment_variable is None
            )
            if unsupported_secrets:
                raise ApplicationPreparationError(
                    "local process runtime only supports secrets projected through declared "
                    f"environment variables: {unsupported_secrets!r}"
                )
        uses_volumes = bool(
            request.volume_bindings
            or request.manifest.volumes
            or any(service.mounts for service in request.manifest.services)
        )
        if uses_volumes:
            if self._workspace_binder is None:
                raise ApplicationPreparationError(
                    "local process runtime requires a Workspace binder for volume bindings"
                )
            self._workspace_binder.validate_request(request)

    def _validate_host_requirements(self, request: ApplicationInstallRequest) -> None:
        rejected = local_host_resource_rejections(
            request.manifest.resources,
            host=self._host_profile,
            runtime_capabilities=self.descriptor.capabilities,
        )
        if rejected:
            raise ApplicationPreparationError(
                "local process host does not satisfy application resource requirements: "
                + ", ".join(rejected)
            )

    async def _materialize_workspace(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> LocalApplicationWorkspaceExecution | None:
        if self._workspace_binder is None:
            return None
        return await self._workspace_binder.materialize(manifest, instance)

    async def _execution_environment(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        workspace_execution: LocalApplicationWorkspaceExecution | None,
    ) -> tuple[dict[str, str], tuple[str, ...], datetime | None]:
        environment = _environment(manifest, instance)
        if not instance.secret_bindings:
            return environment, (), None
        if self._secret_provider is None:
            raise ApplicationRuntimeUnavailableError(
                "local process runtime requires a SecretProvider for secret bindings"
            )

        secret_fields = {field.name: field for field in manifest.secrets}
        sensitive_values: list[str] = []
        earliest_expiry: datetime | None = None
        project_id = (
            workspace_execution.context.project_id if workspace_execution is not None else None
        )
        workspace_id = workspace_execution.workspace_id if workspace_execution is not None else None
        try:
            for name, reference in instance.secret_bindings.items():
                field = secret_fields[name]
                environment_variable = field.environment_variable
                if environment_variable is None:
                    raise ApplicationRuntimeError(
                        "local process runtime cannot project a secret without a declared "
                        "environment variable"
                    )
                material = await self._secret_provider.resolve(
                    reference,
                    SecretAccessContext(
                        consumer_ref=self._secret_consumer_ref,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        action=_SECRET_ACTION,
                        capability_ref=_SECRET_CAPABILITY,
                        purpose=self._secret_purpose,
                        requested_lifetime_seconds=self._secret_lifetime_seconds,
                    ),
                )
                if material.expires_at <= datetime.now(UTC):
                    raise ApplicationRuntimeError(
                        "application secret lease expired before process execution"
                    )
                value = material.reveal()
                environment[environment_variable] = value
                sensitive_values.append(value)
                if earliest_expiry is None or material.expires_at < earliest_expiry:
                    earliest_expiry = material.expires_at
        except ContractError as exc:
            raise ApplicationRuntimeError("application secret resolution failed") from exc
        return environment, tuple(sensitive_values), earliest_expiry

    async def _spawn_service(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        service: ApplicationService,
    ) -> _ManagedProcess:
        del manifest
        argv = service.process + service.command
        environment = self._environments.get(instance.instance_id)
        if environment is None:
            raise ApplicationRuntimeError("application execution environment is unavailable")
        workspace_execution = self._workspace_executions.get(instance.instance_id)
        cwd = (
            self._workspace_binder.service_cwd(workspace_execution, service)
            if self._workspace_binder is not None
            else None
        )
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            cwd=cwd,
        )
        stdout_task = asyncio.create_task(
            self._drain_stream(instance.instance_id, service.service_id, process.stdout, "info")
        )
        stderr_task = asyncio.create_task(
            self._drain_stream(instance.instance_id, service.service_id, process.stderr, "error")
        )
        return _ManagedProcess(
            process=process,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
        )

    async def _drain_stream(
        self,
        instance_id: str,
        service_id: str,
        stream: asyncio.StreamReader | None,
        level: str,
    ) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            message = line.decode(errors="replace").rstrip("\r\n")
            if not message:
                continue
            message = redact_text(message, self._sensitive_values.get(instance_id, ()))
            entries = self._logs.setdefault(
                instance_id,
                deque(maxlen=self._log_capacity),
            )
            entries.append(
                ApplicationLogEntry(
                    message=message,
                    service_id=service_id,
                    level=level,
                )
            )

    async def _wait_until_healthy(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        service: ApplicationService,
        managed: _ManagedProcess,
    ) -> None:
        health_check = service.health_check
        if health_check is None:
            return
        for attempt in range(health_check.retries):
            if managed.process.returncode is not None:
                break
            if await self._probe_service(manifest, instance, service, managed):
                return
            if attempt + 1 < health_check.retries:
                await asyncio.sleep(health_check.interval_seconds)
        raise ApplicationRuntimeError(
            f"application service failed its startup health check: {service.service_id}"
        )

    async def _probe_service(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
        service: ApplicationService,
        managed: _ManagedProcess,
    ) -> bool:
        del manifest
        if managed.process.returncode is not None:
            return False
        health_check = service.health_check
        if health_check is None:
            return True
        if health_check.kind is ApplicationHealthCheckKind.COMMAND:
            environment = self._environments.get(instance.instance_id)
            if environment is None:
                return False
            workspace_execution = self._workspace_executions.get(instance.instance_id)
            cwd = (
                self._workspace_binder.service_cwd(workspace_execution, service)
                if self._workspace_binder is not None
                else None
            )
            return await _probe_command(
                health_check.command,
                environment=environment,
                timeout_seconds=health_check.timeout_seconds,
                cwd=cwd,
            )
        endpoint = next(
            item for item in service.endpoints if item.name == health_check.endpoint_name
        )
        return await _probe_endpoint(
            endpoint.target_port,
            timeout_seconds=health_check.timeout_seconds,
        )

    def _missing_service_state(
        self,
        instance: ApplicationInstance,
        service: ApplicationService,
    ) -> ApplicationServiceState:
        if instance.desired_state is ApplicationDesiredState.STOPPED:
            return ApplicationServiceState(
                service_id=service.service_id,
                observed_state=ApplicationObservedState.STOPPED,
                health=ApplicationHealthStatus.UNKNOWN,
            )
        if instance.desired_state is ApplicationDesiredState.REMOVED:
            return ApplicationServiceState(
                service_id=service.service_id,
                observed_state=ApplicationObservedState.REMOVED,
                health=ApplicationHealthStatus.UNKNOWN,
            )
        if instance.instance_id in self._expired_secret_leases:
            return ApplicationServiceState(
                service_id=service.service_id,
                observed_state=ApplicationObservedState.FAILED,
                health=ApplicationHealthStatus.UNHEALTHY,
                message="application secret lease expired",
            )
        return ApplicationServiceState(
            service_id=service.service_id,
            observed_state=ApplicationObservedState.FAILED,
            health=ApplicationHealthStatus.UNHEALTHY,
            message="runtime process is not owned by this backend instance",
        )

    async def _stop_owned(
        self,
        manifest: ApplicationManifest,
        instance_id: str,
        *,
        cancel_secret_lease: bool = True,
        workspace_outcome: MaterializationOutcome = MaterializationOutcome.SUCCEEDED,
        commit_workspace: bool = False,
    ) -> None:
        if cancel_secret_lease:
            await self._cancel_secret_lease(instance_id)
        owned = self._processes.get(instance_id)
        had_live_process = bool(
            owned and any(managed.process.returncode is None for managed in owned.values())
        )
        if owned:
            for service in reversed(_dependency_order(manifest.services)):
                managed = owned.pop(service.service_id, None)
                if managed is not None:
                    await self._stop_process(managed)
            self._processes.pop(instance_id, None)
        workspace_execution = self._workspace_executions.pop(instance_id, None)
        try:
            if self._workspace_binder is not None:
                await self._workspace_binder.release(
                    workspace_execution,
                    outcome=workspace_outcome,
                    commit=commit_workspace and had_live_process,
                )
        finally:
            self._environments.pop(instance_id, None)
            self._sensitive_values.pop(instance_id, None)

    async def _stop_process(self, managed: _ManagedProcess) -> None:
        process = managed.process
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=self._stop_timeout_seconds)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        await asyncio.gather(
            managed.stdout_task,
            managed.stderr_task,
            return_exceptions=True,
        )

    async def _expire_secret_lease(
        self,
        manifest: ApplicationManifest,
        instance_id: str,
        expires_at: datetime,
    ) -> None:
        delay = max(0.0, (expires_at - datetime.now(UTC)).total_seconds())
        try:
            await asyncio.sleep(delay)
            self._expired_secret_leases.add(instance_id)
            await self._stop_owned(
                manifest,
                instance_id,
                cancel_secret_lease=False,
                workspace_outcome=MaterializationOutcome.FAILED,
                commit_workspace=False,
            )
        except asyncio.CancelledError:
            raise
        finally:
            current = asyncio.current_task()
            if self._secret_lease_tasks.get(instance_id) is current:
                self._secret_lease_tasks.pop(instance_id, None)

    async def _cancel_secret_lease(self, instance_id: str) -> None:
        task = self._secret_lease_tasks.pop(instance_id, None)
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _finish_cleanup(
        self,
        manifest: ApplicationManifest,
        instance_id: str,
        outcome: MaterializationOutcome,
    ) -> None:
        task = asyncio.create_task(
            self._stop_owned(
                manifest,
                instance_id,
                workspace_outcome=outcome,
                commit_workspace=False,
            )
        )
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()


def _dependency_order(services: tuple[ApplicationService, ...]) -> tuple[ApplicationService, ...]:
    remaining = {service.service_id: service for service in services}
    resolved: set[str] = set()
    ordered: list[ApplicationService] = []
    while remaining:
        progressed = False
        for service in services:
            if service.service_id not in remaining:
                continue
            if set(service.depends_on) <= resolved:
                ordered.append(service)
                resolved.add(service.service_id)
                del remaining[service.service_id]
                progressed = True
        if not progressed:
            raise ApplicationPreparationError("application service dependencies are not resolvable")
    return tuple(ordered)


def _environment(manifest: ApplicationManifest, instance: ApplicationInstance) -> dict[str, str]:
    environment = dict(os.environ)
    for field in manifest.configuration:
        if field.environment_variable is None:
            continue
        value = instance.configuration.get(field.name)
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        environment[field.environment_variable] = rendered
    return environment


def _aggregate_state(
    *,
    desired_state: ApplicationDesiredState,
    service_count: int,
    live_count: int,
    unhealthy_count: int,
) -> tuple[ApplicationObservedState, ApplicationHealthStatus]:
    if live_count == service_count:
        if unhealthy_count:
            return ApplicationObservedState.UNHEALTHY, ApplicationHealthStatus.UNHEALTHY
        return ApplicationObservedState.RUNNING, ApplicationHealthStatus.HEALTHY
    if live_count:
        return ApplicationObservedState.DEGRADED, ApplicationHealthStatus.DEGRADED
    if desired_state is ApplicationDesiredState.STOPPED:
        return ApplicationObservedState.STOPPED, ApplicationHealthStatus.UNKNOWN
    if desired_state is ApplicationDesiredState.REMOVED:
        return ApplicationObservedState.REMOVED, ApplicationHealthStatus.UNKNOWN
    return ApplicationObservedState.FAILED, ApplicationHealthStatus.UNHEALTHY


def _endpoint_uri(
    protocol: ApplicationEndpointProtocol,
    target_port: int,
    path: str | None,
) -> str:
    suffix = "" if protocol is ApplicationEndpointProtocol.TCP else (path or "/")
    return f"{protocol.value}://127.0.0.1:{target_port}{suffix}"


async def _probe_command(
    command: tuple[str, ...],
    *,
    environment: dict[str, str],
    timeout_seconds: float,
    cwd: os.PathLike[str] | None = None,
) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
            cwd=cwd,
        )
    except OSError:
        return False
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout_seconds) == 0
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        return False


async def _probe_endpoint(target_port: int, *, timeout_seconds: float) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", target_port),
            timeout=timeout_seconds,
        )
    except (OSError, TimeoutError):
        return False
    del reader
    writer.close()
    await writer.wait_closed()
    return True
