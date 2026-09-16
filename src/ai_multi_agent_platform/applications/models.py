"""Canonical provider-neutral Application Adapter domain models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id
from ai_multi_agent_platform.security import SecretReference

APPLICATION_MANIFEST_SCHEMA_VERSION = "1.0"
_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _name(value: str, field_name: str) -> str:
    if _NAME.fullmatch(value) is None:
        raise ValueError(f"{field_name} must use lowercase identifier syntax")
    return value


def _nonblank_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    return tuple(_nonblank(value, field_name) for value in values)


def _unique_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    copied = _nonblank_strings(values, field_name)
    if len(copied) != len(set(copied)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return copied


def _environment_name(value: str | None, field_name: str) -> str | None:
    if value is not None and _ENVIRONMENT_NAME.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a valid environment variable name")
    return value


def _validate_configuration_value(value: JsonValue, value_type: ApplicationConfigValueType) -> None:
    if value is None:
        return
    if value_type is ApplicationConfigValueType.STRING and not isinstance(value, str):
        raise ValueError("configuration value must be a string")
    if value_type is ApplicationConfigValueType.INTEGER and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        raise ValueError("configuration value must be an integer")
    if value_type is ApplicationConfigValueType.NUMBER and (
        not isinstance(value, int | float) or isinstance(value, bool)
    ):
        raise ValueError("configuration value must be a number")
    if value_type is ApplicationConfigValueType.BOOLEAN and not isinstance(value, bool):
        raise ValueError("configuration value must be a boolean")


class ApplicationDesiredState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    REMOVED = "removed"


class ApplicationObservedState(StrEnum):
    PREPARING = "preparing"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    FAILED = "failed"
    REMOVING = "removing"
    REMOVED = "removed"


class ApplicationHealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ApplicationServiceRuntime(StrEnum):
    OCI_IMAGE = "oci_image"
    PROCESS = "process"


class ApplicationEndpointProtocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"


class ApplicationEndpointExposure(StrEnum):
    INTERNAL = "internal"
    USER = "user"
    API = "api"
    METRICS = "metrics"


class ApplicationVolumeKind(StrEnum):
    WORKSPACE = "workspace"
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"


class ApplicationConfigValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


class ApplicationHealthCheckKind(StrEnum):
    ENDPOINT = "endpoint"
    COMMAND = "command"


class ApplicationUiOpenMode(StrEnum):
    EMBEDDED = "embedded"
    EXTERNAL = "external"


class ApplicationMaturity(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True, slots=True)
class ApplicationConfigurationField:
    name: str
    value_type: ApplicationConfigValueType
    required: bool = False
    default: JsonValue = None
    mutable: bool = True
    environment_variable: str | None = None

    def __post_init__(self) -> None:
        _name(self.name, "configuration field name")
        _environment_name(self.environment_variable, "configuration environment_variable")
        _validate_configuration_value(self.default, self.value_type)


@dataclass(frozen=True, slots=True)
class ApplicationSecretField:
    name: str
    required: bool = True
    environment_variable: str | None = None

    def __post_init__(self) -> None:
        _name(self.name, "secret field name")
        _environment_name(self.environment_variable, "secret environment_variable")


@dataclass(frozen=True, slots=True)
class ApplicationEndpoint:
    name: str
    target_port: int
    protocol: ApplicationEndpointProtocol = ApplicationEndpointProtocol.HTTP
    exposure: ApplicationEndpointExposure = ApplicationEndpointExposure.INTERNAL
    path: str | None = None

    def __post_init__(self) -> None:
        _name(self.name, "endpoint name")
        if self.target_port < 1 or self.target_port > 65535:
            raise ValueError("target_port must be between 1 and 65535")
        if self.path is not None:
            if self.protocol is ApplicationEndpointProtocol.TCP:
                raise ValueError("TCP endpoints cannot declare an HTTP path")
            if not self.path.startswith("/"):
                raise ValueError("endpoint path must start with '/'")


@dataclass(frozen=True, slots=True)
class ApplicationHealthCheck:
    kind: ApplicationHealthCheckKind
    endpoint_name: str | None = None
    command: tuple[str, ...] = ()
    interval_seconds: float = 10.0
    timeout_seconds: float = 2.0
    retries: int = 3

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("health check interval_seconds must be > 0")
        if self.timeout_seconds <= 0:
            raise ValueError("health check timeout_seconds must be > 0")
        if self.retries < 1:
            raise ValueError("health check retries must be >= 1")
        if self.kind is ApplicationHealthCheckKind.ENDPOINT:
            if self.endpoint_name is None:
                raise ValueError("endpoint health checks require endpoint_name")
            _name(self.endpoint_name, "health check endpoint_name")
            if self.command:
                raise ValueError("endpoint health checks cannot declare command")
        else:
            if self.endpoint_name is not None:
                raise ValueError("command health checks cannot declare endpoint_name")
            if not self.command:
                raise ValueError("command health checks require command")
            _nonblank_strings(self.command, "health check command")


@dataclass(frozen=True, slots=True)
class ApplicationVolume:
    name: str
    kind: ApplicationVolumeKind
    required: bool = True

    def __post_init__(self) -> None:
        _name(self.name, "volume name")


@dataclass(frozen=True, slots=True)
class ApplicationVolumeMount:
    volume_name: str
    target: str
    read_only: bool = False

    def __post_init__(self) -> None:
        _name(self.volume_name, "volume mount volume_name")
        _nonblank(self.target, "volume mount target")


@dataclass(frozen=True, slots=True)
class ApplicationService:
    service_id: str
    runtime: ApplicationServiceRuntime
    image: str | None = None
    process: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    endpoints: tuple[ApplicationEndpoint, ...] = ()
    mounts: tuple[ApplicationVolumeMount, ...] = ()
    health_check: ApplicationHealthCheck | None = None

    def __post_init__(self) -> None:
        _name(self.service_id, "service_id")
        if self.runtime is ApplicationServiceRuntime.OCI_IMAGE:
            if self.image is None:
                raise ValueError("OCI image services require image")
            _nonblank(self.image, "service image")
            if self.process:
                raise ValueError("OCI image services cannot declare process")
        else:
            if self.image is not None:
                raise ValueError("process services cannot declare image")
            if not self.process:
                raise ValueError("process services require process")
            _nonblank_strings(self.process, "service process")
        if self.command:
            _nonblank_strings(self.command, "service command")
        depends_on = _unique_strings(self.depends_on, "service dependencies")
        if self.service_id in depends_on:
            raise ValueError("service cannot depend on itself")
        endpoint_names = [endpoint.name for endpoint in self.endpoints]
        if len(endpoint_names) != len(set(endpoint_names)):
            raise ValueError("service endpoint names must be unique")
        mount_targets = [mount.target for mount in self.mounts]
        if len(mount_targets) != len(set(mount_targets)):
            raise ValueError("service mount targets must be unique")
        if (
            self.health_check is not None
            and self.health_check.kind is ApplicationHealthCheckKind.ENDPOINT
            and self.health_check.endpoint_name not in set(endpoint_names)
        ):
            raise ValueError("health check references an unknown service endpoint")


@dataclass(frozen=True, slots=True)
class ApplicationResourceRequirements:
    cpu_cores: float | None = None
    memory_bytes: int | None = None
    gpu_count: int = 0
    disk_bytes: int | None = None
    architectures: tuple[str, ...] = ()
    operating_systems: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    required_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.cpu_cores is not None and self.cpu_cores <= 0:
            raise ValueError("cpu_cores must be > 0 when provided")
        if self.memory_bytes is not None and self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be > 0 when provided")
        if self.gpu_count < 0:
            raise ValueError("gpu_count must be >= 0")
        if self.disk_bytes is not None and self.disk_bytes <= 0:
            raise ValueError("disk_bytes must be > 0 when provided")
        for field_name in (
            "architectures",
            "operating_systems",
            "required_capabilities",
            "required_labels",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_strings(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class ApplicationUi:
    endpoint_ref: str
    open_mode: ApplicationUiOpenMode = ApplicationUiOpenMode.EXTERNAL

    def __post_init__(self) -> None:
        _nonblank(self.endpoint_ref, "ui endpoint_ref")


@dataclass(frozen=True, slots=True)
class ApplicationResourceAssociation:
    media_types: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.media_types and not self.resource_types:
            raise ValueError("resource association requires media_types or resource_types")
        object.__setattr__(
            self,
            "media_types",
            _unique_strings(self.media_types, "association media_types"),
        )
        object.__setattr__(
            self,
            "resource_types",
            _unique_strings(self.resource_types, "association resource_types"),
        )


@dataclass(frozen=True, slots=True)
class ApplicationManifest:
    application_id: str
    name: str
    version: str
    description: str
    services: tuple[ApplicationService, ...]
    volumes: tuple[ApplicationVolume, ...] = ()
    configuration: tuple[ApplicationConfigurationField, ...] = ()
    secrets: tuple[ApplicationSecretField, ...] = ()
    resources: ApplicationResourceRequirements = field(
        default_factory=ApplicationResourceRequirements
    )
    ui: ApplicationUi | None = None
    resource_associations: tuple[ApplicationResourceAssociation, ...] = ()
    maturity: ApplicationMaturity = ApplicationMaturity.BETA
    runtime_requirements: tuple[str, ...] = ()
    schema_version: str = APPLICATION_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.application_id, "application")
        _nonblank(self.name, "application name")
        _nonblank(self.version, "application version")
        _nonblank(self.description, "application description")
        if self.schema_version != APPLICATION_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported application manifest schema_version")
        if not self.services:
            raise ValueError("application manifest requires at least one service")
        _validate_manifest_services(self.services)
        _validate_manifest_volumes(self.services, self.volumes)
        _validate_manifest_inputs(self.configuration, self.secrets)
        _validate_manifest_ui(self.services, self.ui)
        object.__setattr__(
            self,
            "runtime_requirements",
            _unique_strings(self.runtime_requirements, "runtime_requirements"),
        )

    def endpoint_refs(self) -> tuple[str, ...]:
        return tuple(
            f"{service.service_id}.{endpoint.name}"
            for service in self.services
            for endpoint in service.endpoints
        )


def _validate_manifest_services(services: tuple[ApplicationService, ...]) -> None:
    service_ids = [service.service_id for service in services]
    if len(service_ids) != len(set(service_ids)):
        raise ValueError("application service ids must be unique")
    service_id_set = set(service_ids)
    for service in services:
        unknown = set(service.depends_on) - service_id_set
        if unknown:
            raise ValueError(
                f"service {service.service_id!r} depends on unknown services: {sorted(unknown)!r}"
            )
    _validate_service_dependency_graph(services)


def _validate_manifest_volumes(
    services: tuple[ApplicationService, ...],
    volumes: tuple[ApplicationVolume, ...],
) -> None:
    volume_names = [volume.name for volume in volumes]
    if len(volume_names) != len(set(volume_names)):
        raise ValueError("application volume names must be unique")
    volume_name_set = set(volume_names)
    for service in services:
        for mount in service.mounts:
            if mount.volume_name not in volume_name_set:
                raise ValueError(
                    f"service {service.service_id!r} mounts unknown volume {mount.volume_name!r}"
                )


def _validate_manifest_inputs(
    configuration: tuple[ApplicationConfigurationField, ...],
    secrets: tuple[ApplicationSecretField, ...],
) -> None:
    config_names = [item.name for item in configuration]
    if len(config_names) != len(set(config_names)):
        raise ValueError("configuration field names must be unique")
    secret_names = [item.name for item in secrets]
    if len(secret_names) != len(set(secret_names)):
        raise ValueError("secret field names must be unique")
    if set(config_names) & set(secret_names):
        raise ValueError("configuration and secret names must not overlap")


def _validate_manifest_ui(
    services: tuple[ApplicationService, ...],
    ui: ApplicationUi | None,
) -> None:
    if ui is None:
        return
    endpoint_refs = {
        f"{service.service_id}.{endpoint.name}"
        for service in services
        for endpoint in service.endpoints
    }
    if ui.endpoint_ref not in endpoint_refs:
        raise ValueError("ui endpoint_ref must reference a declared service endpoint")


def _validate_service_dependency_graph(services: tuple[ApplicationService, ...]) -> None:
    dependencies = {service.service_id: service.depends_on for service in services}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service_id: str) -> None:
        if service_id in visited:
            return
        if service_id in visiting:
            raise ValueError("application service dependencies must be acyclic")
        visiting.add(service_id)
        for dependency in dependencies[service_id]:
            visit(dependency)
        visiting.remove(service_id)
        visited.add(service_id)

    for service_id in dependencies:
        visit(service_id)


@dataclass(frozen=True, slots=True)
class ApplicationVolumeBinding:
    volume_name: str
    kind: ApplicationVolumeKind
    source_ref: str
    read_only: bool = False

    def __post_init__(self) -> None:
        _name(self.volume_name, "volume binding volume_name")
        _nonblank(self.source_ref, "volume binding source_ref")
        if self.kind is ApplicationVolumeKind.WORKSPACE:
            validate_id(self.source_ref, "workspace")
        elif self.kind is ApplicationVolumeKind.PERSISTENT:
            if self.source_ref.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE_PATH.match(
                self.source_ref
            ):
                raise ValueError(
                    "persistent volume source_ref must be a platform-managed reference, "
                    "not a host filesystem path"
                )
        else:
            raise ValueError("ephemeral volumes are runtime-managed and cannot be externally bound")


def _validated_install_configuration(
    manifest: ApplicationManifest,
    provided: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    fields = {item.name: item for item in manifest.configuration}
    configuration = dict(provided)
    unknown = set(configuration) - set(fields)
    if unknown:
        raise ValueError(f"unknown application configuration fields: {sorted(unknown)!r}")
    for item in fields.values():
        if item.name in configuration:
            _validate_configuration_value(configuration[item.name], item.value_type)
        elif item.required and item.default is None:
            raise ValueError(f"missing required application configuration field: {item.name}")
    return configuration


def _validated_install_secrets(
    manifest: ApplicationManifest,
    provided: Mapping[str, SecretReference],
) -> dict[str, SecretReference]:
    fields = {item.name: item for item in manifest.secrets}
    secrets = dict(provided)
    unknown = set(secrets) - set(fields)
    if unknown:
        raise ValueError(f"unknown application secret bindings: {sorted(unknown)!r}")
    for name, reference in secrets.items():
        if not isinstance(reference, SecretReference):
            raise ValueError(f"secret binding {name!r} must be a SecretReference")
    missing = [item.name for item in fields.values() if item.required and item.name not in secrets]
    if missing:
        raise ValueError(f"missing required application secrets: {sorted(missing)!r}")
    return secrets


def _validate_install_volumes(
    manifest: ApplicationManifest,
    bindings: tuple[ApplicationVolumeBinding, ...],
) -> None:
    volumes = {item.name: item for item in manifest.volumes}
    seen: set[str] = set()
    for binding in bindings:
        if binding.volume_name in seen:
            raise ValueError("application volume bindings must be unique by volume_name")
        seen.add(binding.volume_name)
        volume = volumes.get(binding.volume_name)
        if volume is None:
            raise ValueError(f"unknown application volume binding: {binding.volume_name}")
        if binding.kind is not volume.kind:
            raise ValueError(
                f"volume binding kind for {binding.volume_name!r} does not match manifest"
            )
    missing = [
        volume.name
        for volume in volumes.values()
        if volume.required
        and volume.kind is not ApplicationVolumeKind.EPHEMERAL
        and volume.name not in seen
    ]
    if missing:
        raise ValueError(f"missing required application volumes: {sorted(missing)!r}")


@dataclass(frozen=True, slots=True)
class ApplicationInstallRequest:
    manifest: ApplicationManifest
    configuration: Mapping[str, JsonValue] = field(default_factory=dict)
    secret_bindings: Mapping[str, SecretReference] = field(default_factory=dict)
    volume_bindings: tuple[ApplicationVolumeBinding, ...] = ()
    node_id: str | None = None

    def __post_init__(self) -> None:
        if self.node_id is not None:
            validate_id(self.node_id, "node")
        configuration = _validated_install_configuration(self.manifest, self.configuration)
        secrets = _validated_install_secrets(self.manifest, self.secret_bindings)
        _validate_install_volumes(self.manifest, self.volume_bindings)
        object.__setattr__(self, "configuration", MappingProxyType(configuration))
        object.__setattr__(self, "secret_bindings", MappingProxyType(secrets))

    def resolved_configuration(self) -> Mapping[str, JsonValue]:
        resolved: dict[str, JsonValue] = {
            item.name: item.default
            for item in self.manifest.configuration
            if item.default is not None
        }
        resolved.update(self.configuration)
        return MappingProxyType(resolved)


@dataclass(frozen=True, slots=True)
class ApplicationEndpointResolution:
    endpoint_ref: str
    uri: str
    exposure: ApplicationEndpointExposure

    def __post_init__(self) -> None:
        _nonblank(self.endpoint_ref, "endpoint_ref")
        _nonblank(self.uri, "endpoint uri")


@dataclass(frozen=True, slots=True)
class ApplicationServiceState:
    service_id: str
    observed_state: ApplicationObservedState
    health: ApplicationHealthStatus
    message: str | None = None

    def __post_init__(self) -> None:
        _name(self.service_id, "service state service_id")
        if self.message is not None:
            _nonblank(self.message, "service state message")


@dataclass(frozen=True, slots=True)
class ApplicationInstance:
    application_id: str
    application_version: str
    runtime_id: str
    desired_state: ApplicationDesiredState
    observed_state: ApplicationObservedState
    health: ApplicationHealthStatus
    instance_id: str = field(default_factory=lambda: new_id("application_instance"))
    node_id: str | None = None
    configuration: Mapping[str, JsonValue] = field(default_factory=dict)
    secret_bindings: Mapping[str, SecretReference] = field(default_factory=dict)
    volume_bindings: tuple[ApplicationVolumeBinding, ...] = ()
    service_states: tuple[ApplicationServiceState, ...] = ()
    endpoints: tuple[ApplicationEndpointResolution, ...] = ()
    revision: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.application_id, "application")
        validate_id(self.instance_id, "application_instance")
        _nonblank(self.application_version, "application_version")
        _nonblank(self.runtime_id, "runtime_id")
        if self.node_id is not None:
            validate_id(self.node_id, "node")
        if self.revision < 1:
            raise ValueError("application instance revision must be >= 1")
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        service_ids = [state.service_id for state in self.service_states]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("application service states must be unique by service_id")
        endpoint_refs = [endpoint.endpoint_ref for endpoint in self.endpoints]
        if len(endpoint_refs) != len(set(endpoint_refs)):
            raise ValueError("application endpoints must be unique by endpoint_ref")
        object.__setattr__(
            self,
            "configuration",
            MappingProxyType(dict(self.configuration)),
        )
        object.__setattr__(
            self,
            "secret_bindings",
            MappingProxyType(dict(self.secret_bindings)),
        )

    def open_endpoint(self, manifest: ApplicationManifest) -> ApplicationEndpointResolution | None:
        if manifest.ui is None:
            return None
        return next(
            (
                endpoint
                for endpoint in self.endpoints
                if endpoint.endpoint_ref == manifest.ui.endpoint_ref
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class ApplicationLogEntry:
    message: str
    service_id: str | None = None
    level: str = "info"
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _nonblank(self.message, "log message")
        _nonblank(self.level, "log level")
        if self.service_id is not None:
            _name(self.service_id, "log service_id")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("log timestamp must be timezone-aware")
