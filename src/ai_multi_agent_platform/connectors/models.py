"""Canonical connector, connection, external-resource, event and sync models."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    HealthStatus,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.security import SecretReference


def utc_now() -> datetime:
    return datetime.now(UTC)


def connector_definition_id(connector_type_id: str, version: str) -> str:
    """Return the platform-owned stable identity for one connector type/version."""

    if not connector_type_id.strip():
        raise ValueError("connector_type_id must not be blank")
    if not version.strip():
        raise ValueError("connector version must not be blank")
    identity_key = json.dumps(
        [connector_type_id, version],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"connector_definition_{uuid5(NAMESPACE_URL, identity_key)}"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _nonblank_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain blank values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(values)


class ConnectionStatus(StrEnum):
    CONFIGURING = "configuring"
    READY = "ready"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    ERROR = "error"


class SyncStatus(StrEnum):
    NEVER = "never"
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SyncMode(StrEnum):
    """Explicit caller intent for synchronization/recovery behavior."""

    INCREMENTAL = "incremental"
    RESYNC = "resync"
    REBUILD = "rebuild"


class ConflictPolicy(StrEnum):
    REMOTE_WINS = "remote_wins"
    LOCAL_WINS = "local_wins"
    MANUAL = "manual"
    REBUILD = "rebuild"


@dataclass(frozen=True, slots=True)
class ConnectorDefinition:
    """Canonical backend-neutral description of one connector type/version."""

    id: str
    connector_type_id: str
    name: str
    version: str
    description: str = ""
    supported_operations: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    authentication_requirements: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    configuration_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    health_semantics: Mapping[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "connector_definition")
        if not self.connector_type_id.strip():
            raise ValueError("connector_type_id must not be blank")
        if not self.name.strip():
            raise ValueError("connector name must not be blank")
        if not self.version.strip():
            raise ValueError("connector version must not be blank")
        object.__setattr__(
            self,
            "supported_operations",
            _nonblank_tuple(self.supported_operations, "supported_operations"),
        )
        object.__setattr__(self, "features", _nonblank_tuple(self.features, "features"))
        object.__setattr__(
            self,
            "authentication_requirements",
            _nonblank_tuple(self.authentication_requirements, "authentication_requirements"),
        )
        object.__setattr__(
            self, "resource_types", _nonblank_tuple(self.resource_types, "resource_types")
        )
        object.__setattr__(self, "actions", _nonblank_tuple(self.actions, "actions"))
        object.__setattr__(self, "event_types", _nonblank_tuple(self.event_types, "event_types"))
        object.__setattr__(self, "configuration_schema", _freeze_mapping(self.configuration_schema))
        object.__setattr__(self, "health_semantics", _freeze_mapping(self.health_semantics))


@dataclass(frozen=True, slots=True)
class Connection:
    """One configured account/endpoint for a connector definition."""

    id: str
    connector_type_id: str
    connector_version: str
    owner_type: str
    owner_id: str
    display_name: str
    project_id: str | None = None
    organization_id: str | None = None
    endpoint_metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    secret_references: tuple[SecretReference, ...] = ()
    requested_scopes: tuple[str, ...] = ()
    granted_scopes: tuple[str, ...] = ()
    enabled: bool = True
    status: ConnectionStatus = ConnectionStatus.CONFIGURING
    health: HealthStatus = HealthStatus.UNKNOWN
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_checked_at: datetime | None = None
    revision: int = 1
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "connection")
        if not self.connector_type_id.strip():
            raise ValueError("connector_type_id must not be blank")
        if not self.connector_version.strip():
            raise ValueError("connector_version must not be blank")
        if not self.owner_type.strip() or not self.owner_id.strip():
            raise ValueError("connection owner_type/owner_id must not be blank")
        if not self.display_name.strip():
            raise ValueError("connection display_name must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None and not self.organization_id.strip():
            raise ValueError("organization_id must not be blank when provided")
        if self.revision < 1:
            raise ValueError("connection revision must be at least 1")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.last_checked_at is not None:
            _require_aware(self.last_checked_at, "last_checked_at")
        object.__setattr__(self, "endpoint_metadata", _freeze_mapping(self.endpoint_metadata))
        object.__setattr__(
            self,
            "requested_scopes",
            _nonblank_tuple(self.requested_scopes, "requested_scopes"),
        )
        object.__setattr__(
            self,
            "granted_scopes",
            _nonblank_tuple(self.granted_scopes, "granted_scopes"),
        )


@dataclass(frozen=True, slots=True)
class ExternalNativeReference:
    """Namespaced provider-native identity; never a canonical platform ID."""

    namespace: str
    native_id: str

    def __post_init__(self) -> None:
        if not self.namespace.strip() or any(char.isspace() for char in self.namespace):
            raise ValueError("external namespace must be non-blank and contain no spaces")
        if not self.native_id.strip():
            raise ValueError("external native_id must not be blank")


@dataclass(frozen=True, slots=True)
class ExternalResourceReference:
    """Canonical reference to an externally-owned object."""

    id: str
    connection_id: str
    resource_type: str
    native_reference: ExternalNativeReference
    canonical_url: str | None = None
    version: str | None = None
    revision: str | None = None
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "external_resource")
        validate_id(self.connection_id, "connection")
        if not self.resource_type.strip():
            raise ValueError("resource_type must not be blank")
        for name in ("canonical_url", "version", "revision"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "resource_type": self.resource_type,
            "native_reference": {
                "namespace": self.native_reference.namespace,
                "native_id": self.native_reference.native_id,
            },
            "canonical_url": self.canonical_url,
            "version": self.version,
            "revision": self.revision,
            "provenance": dict(self.provenance),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ConnectorEvent:
    """Provider-neutral external event hook; events are evidence, not execution authority."""

    id: str
    connector_type_id: str
    connection_id: str
    event_type: str
    native_reference: ExternalNativeReference
    schema_version: str
    dedupe_key: str
    received_at: datetime
    project_id: str | None = None
    resource_id: str | None = None
    verified: bool = False
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    payload: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "connector_event")
        validate_id(self.connection_id, "connection")
        for name in ("connector_type_id", "event_type", "schema_version", "dedupe_key"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.resource_id is not None:
            validate_id(self.resource_id, "external_resource")
        _require_aware(self.received_at, "received_at")
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class SyncCheckpoint:
    """Connector-owned synchronization state for one remote stream/resource set."""

    connection_id: str
    stream: str
    cursor: str | None = None
    last_successful_sync: datetime | None = None
    remote_revision: str | None = None
    status: SyncStatus = SyncStatus.NEVER
    retry_count: int = 0
    error_code: str | None = None
    dedupe_mapping: Mapping[str, JsonValue] = field(default_factory=dict)
    conflict_policy: ConflictPolicy = ConflictPolicy.REMOTE_WINS
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.connection_id, "connection")
        if not self.stream.strip():
            raise ValueError("sync stream must not be blank")
        if self.retry_count < 0:
            raise ValueError("retry_count must not be negative")
        for name in ("cursor", "remote_revision", "error_code"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")
        if self.last_successful_sync is not None:
            _require_aware(self.last_successful_sync, "last_successful_sync")
        _require_aware(self.updated_at, "updated_at")
        object.__setattr__(self, "dedupe_mapping", _freeze_mapping(self.dedupe_mapping))


@dataclass(frozen=True, slots=True)
class ConnectorResourceQuery:
    connection_id: str
    resource_type: str
    context: OperationContext
    query: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.connection_id, "connection")
        if not self.resource_type.strip():
            raise ValueError("resource_type must not be blank")
        object.__setattr__(self, "query", _freeze_mapping(self.query))


@dataclass(frozen=True, slots=True)
class ConnectorActionInvocation:
    invocation_id: str
    connection_id: str
    action: str
    arguments: Mapping[str, JsonValue]
    context: OperationContext

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id must not be blank")
        validate_id(self.connection_id, "connection")
        if not self.action.strip():
            raise ValueError("action must not be blank")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class ConnectorActionResult:
    invocation_id: str
    output: JsonValue
    resource_refs: tuple[ExternalResourceReference, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id must not be blank")


@dataclass(frozen=True, slots=True)
class ConnectorSyncRequest:
    connection_id: str
    stream: str
    context: OperationContext
    checkpoint: SyncCheckpoint | None = None
    mode: SyncMode = SyncMode.INCREMENTAL

    def __post_init__(self) -> None:
        validate_id(self.connection_id, "connection")
        if not self.stream.strip():
            raise ValueError("stream must not be blank")
        if self.checkpoint is not None:
            if self.checkpoint.connection_id != self.connection_id:
                raise ValueError("checkpoint connection_id must match sync request")
            if self.checkpoint.stream != self.stream:
                raise ValueError("checkpoint stream must match sync request")


@dataclass(frozen=True, slots=True)
class ConnectorSyncResult:
    checkpoint: SyncCheckpoint
    resources: tuple[ExternalResourceReference, ...] = ()
    events: tuple[ConnectorEvent, ...] = ()
