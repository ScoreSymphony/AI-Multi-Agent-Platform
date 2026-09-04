"""Portable connector configuration metadata for issue #79."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from ai_multi_agent_platform.connectors.models import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    connector_definition_id,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue
from ai_multi_agent_platform.security import SecretReference

from .dependencies import resource_dependency
from .models import (
    DependencyKind,
    DependencyRequirement,
    ExcludedState,
    ExclusionCategory,
    IdPolicy,
    PortableResource,
)
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

CONNECTION_PORTABLE_SCHEMA_VERSION = "1"
CONNECTION_RESOURCE_TYPE = "connection"


@dataclass(frozen=True, slots=True)
class ConnectorRequirementMetadata:
    """Provider-neutral connector contract required by one portable Connection."""

    definition_id: str
    connector_type_id: str
    version: str
    supported_operations: tuple[str, ...]
    features: tuple[str, ...]
    authentication_requirements: tuple[str, ...]
    resource_types: tuple[str, ...]
    actions: tuple[str, ...]
    event_types: tuple[str, ...]
    configuration_schema: dict[str, JsonValue]

    @classmethod
    def from_definition(cls, definition: ConnectorDefinition) -> ConnectorRequirementMetadata:
        expected_id = connector_definition_id(definition.connector_type_id, definition.version)
        if definition.id != expected_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector definition has a noncanonical identity",
                details={"expected_id": expected_id, "actual_id": definition.id},
            )
        return cls(
            definition_id=definition.id,
            connector_type_id=definition.connector_type_id,
            version=definition.version,
            supported_operations=definition.supported_operations,
            features=definition.features,
            authentication_requirements=definition.authentication_requirements,
            resource_types=definition.resource_types,
            actions=definition.actions,
            event_types=definition.event_types,
            configuration_schema=dict(definition.configuration_schema),
        )


@dataclass(frozen=True, slots=True)
class ConnectionPortableSnapshot:
    """Portable Connection configuration with destination runtime state disabled."""

    connection: Connection
    connector_requirement: ConnectorRequirementMetadata
    source_enabled: bool

    def __post_init__(self) -> None:
        connection = self.connection
        requirement = self.connector_requirement
        if connection.connector_type_id != requirement.connector_type_id:
            raise ValueError("portable Connection connector type does not match its requirement")
        if connection.connector_version != requirement.version:
            raise ValueError("portable Connection connector version does not match its requirement")
        if connection.enabled:
            raise ValueError("portable Connection target state must be disabled")
        if connection.status is not ConnectionStatus.DISABLED:
            raise ValueError("portable Connection target status must be disabled")
        if connection.health is not HealthStatus.UNAVAILABLE:
            raise ValueError("portable Connection target health must be unavailable")
        if connection.granted_scopes:
            raise ValueError("portable Connection cannot carry destination granted scopes")
        if connection.last_checked_at is not None:
            raise ValueError("portable Connection cannot carry destination health-check state")
        if connection.adapter_metadata:
            raise ValueError("portable Connection cannot carry provider adapter metadata")


def snapshot_connection(
    connection: Connection,
    definition: ConnectorDefinition,
) -> ConnectionPortableSnapshot:
    """Project one Connection into configuration-only portable state."""

    requirement = ConnectorRequirementMetadata.from_definition(definition)
    if connection.connector_type_id != requirement.connector_type_id:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Connection connector type does not match ConnectorDefinition",
        )
    if connection.connector_version != requirement.version:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Connection connector version does not match ConnectorDefinition",
        )
    portable = replace(
        connection,
        granted_scopes=(),
        enabled=False,
        status=ConnectionStatus.DISABLED,
        health=HealthStatus.UNAVAILABLE,
        last_checked_at=None,
        adapter_metadata=(),
    )
    return ConnectionPortableSnapshot(
        connection=portable,
        connector_requirement=requirement,
        source_enabled=connection.enabled,
    )


def connection_runtime_exclusions(connection_id: str) -> tuple[ExcludedState, ...]:
    """Describe provider/runtime Connection state deliberately omitted from portability."""

    return (
        ExcludedState(
            category=ExclusionCategory.PROVIDER_PRIVATE_STATE,
            path="$.connection.adapter_metadata",
            reason="provider-private adapter metadata is recreated by the destination connector",
            resource_type=CONNECTION_RESOURCE_TYPE,
            resource_id=connection_id,
        ),
        ExcludedState(
            category=ExclusionCategory.BACKEND_RUNTIME_STATE,
            path="$.connection.health_state",
            reason=(
                "health/status/last-check state is destination runtime evidence and is recomputed "
                "after explicit activation"
            ),
            resource_type=CONNECTION_RESOURCE_TYPE,
            resource_id=connection_id,
        ),
        ExcludedState(
            category=ExclusionCategory.BACKEND_RUNTIME_STATE,
            path="$.connection.granted_scopes",
            reason="remote granted scopes are re-established by the destination connector",
            resource_type=CONNECTION_RESOURCE_TYPE,
            resource_id=connection_id,
        ),
        ExcludedState(
            category=ExclusionCategory.BACKEND_RUNTIME_STATE,
            path="$.connection.sync_checkpoints",
            reason=(
                "provider cursors, retry state and sync checkpoints are not portable "
                "configuration"
            ),
            resource_type=CONNECTION_RESOURCE_TYPE,
            resource_id=connection_id,
        ),
    )


def secret_reference_requirement_identifier(reference: SecretReference) -> str:
    """Return a deterministic human-readable requirement identifier for a SecretReference."""

    version = reference.version or "current"
    return f"{reference.provider}/{reference.secret_id}@{version}#{reference.scope}"


class ConnectionPortableCodec:
    resource_type = CONNECTION_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        snapshot = _require_snapshot(value)
        connection = snapshot.connection
        return ResourceExport(
            resource_id=connection.id,
            resource_version=CONNECTION_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": CONNECTION_PORTABLE_SCHEMA_VERSION,
                "source_enabled": snapshot.source_enabled,
                "activation_required": snapshot.source_enabled,
                "runtime_state_included": False,
                "connector_requirement": _connector_requirement_to_json(
                    snapshot.connector_requirement
                ),
                "connection": _connection_to_json(connection),
            },
            id_policy=self.id_policy,
            dependencies=_connection_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Connection codec cannot deserialize resource type {resource.resource_type!r}",
            )
        try:
            if resource.payload.get("schema_version") != CONNECTION_PORTABLE_SCHEMA_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "unsupported portable Connection schema version",
                    details={"supported_schema_version": CONNECTION_PORTABLE_SCHEMA_VERSION},
                )
            if resource.payload.get("runtime_state_included") is not False:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Connection must explicitly exclude provider runtime state",
                )
            source_enabled = _bool_value(resource.payload.get("source_enabled"), "source_enabled")
            requirement = _connector_requirement_from_json(
                resource.payload.get("connector_requirement")
            )
            connection = _connection_from_json(resource.payload.get("connection"))
            if connection.id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Connection payload identity disagrees with resource ID",
                )
            if connection.connector_type_id != requirement.connector_type_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Connection connector type disagrees with requirement metadata",
                )
            if connection.connector_version != requirement.version:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Connection connector version disagrees with requirement metadata",
                )
            target_id = context.remap(CONNECTION_RESOURCE_TYPE, connection.id)
            project_id = (
                None
                if connection.project_id is None
                else context.remap("project", connection.project_id)
            )
            remapped = replace(
                connection,
                id=target_id,
                project_id=project_id,
                granted_scopes=(),
                enabled=False,
                status=ConnectionStatus.DISABLED,
                health=HealthStatus.UNAVAILABLE,
                last_checked_at=None,
                adapter_metadata=(),
            )
            return ConnectionPortableSnapshot(
                connection=remapped,
                connector_requirement=requirement,
                source_enabled=source_enabled,
            )
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Connection payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_connector_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(ConnectionPortableCodec(id_policy=id_policy))


def _connection_dependencies(
    snapshot: ConnectionPortableSnapshot,
) -> tuple[DependencyRequirement, ...]:
    connection = snapshot.connection
    requirement = snapshot.connector_requirement
    dependencies: list[DependencyRequirement] = [
        DependencyRequirement(
            kind=DependencyKind.CONNECTOR,
            identifier=requirement.definition_id,
            required=True,
            version_constraint=requirement.version,
            purpose=(
                f"Connector {requirement.connector_type_id!r} implementation required for "
                "Connection"
            ),
        )
    ]
    if connection.project_id is not None:
        dependencies.append(
            resource_dependency(
                "project",
                connection.project_id,
                purpose="Connection project scope",
            )
        )
    dependencies.extend(
        DependencyRequirement(
            kind=DependencyKind.SECRET,
            identifier=secret_reference_requirement_identifier(reference),
            required=True,
            purpose=(
                "Connection secret reference; target must bind a compatible local secret "
                f"for provider {reference.provider!r} and scope {reference.scope!r}"
            ),
        )
        for reference in connection.secret_references
    )
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (item.kind.value, item.identifier, item.purpose or ""),
        )
    )


def _connector_requirement_to_json(
    requirement: ConnectorRequirementMetadata,
) -> dict[str, JsonValue]:
    return {
        "definition_id": requirement.definition_id,
        "connector_type_id": requirement.connector_type_id,
        "version": requirement.version,
        "supported_operations": list(requirement.supported_operations),
        "features": list(requirement.features),
        "authentication_requirements": list(requirement.authentication_requirements),
        "resource_types": list(requirement.resource_types),
        "actions": list(requirement.actions),
        "event_types": list(requirement.event_types),
        "configuration_schema": dict(requirement.configuration_schema),
    }


def _connector_requirement_from_json(value: JsonValue | None) -> ConnectorRequirementMetadata:
    data = _object(value, "connector_requirement")
    requirement = ConnectorRequirementMetadata(
        definition_id=_string(data, "definition_id"),
        connector_type_id=_string(data, "connector_type_id"),
        version=_string(data, "version"),
        supported_operations=_string_tuple(
            data.get("supported_operations"), "supported_operations"
        ),
        features=_string_tuple(data.get("features"), "features"),
        authentication_requirements=_string_tuple(
            data.get("authentication_requirements"), "authentication_requirements"
        ),
        resource_types=_string_tuple(data.get("resource_types"), "resource_types"),
        actions=_string_tuple(data.get("actions"), "actions"),
        event_types=_string_tuple(data.get("event_types"), "event_types"),
        configuration_schema=_object(data.get("configuration_schema"), "configuration_schema"),
    )
    expected_id = connector_definition_id(requirement.connector_type_id, requirement.version)
    if requirement.definition_id != expected_id:
        raise ValueError("connector requirement definition_id is not canonical")
    return requirement


def _connection_to_json(connection: Connection) -> dict[str, JsonValue]:
    return {
        "id": connection.id,
        "connector_type_id": connection.connector_type_id,
        "connector_version": connection.connector_version,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "display_name": connection.display_name,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "endpoint_metadata": dict(connection.endpoint_metadata),
        "secret_references": [reference.to_dict() for reference in connection.secret_references],
        "requested_scopes": list(connection.requested_scopes),
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
        "revision": connection.revision,
    }


def _connection_from_json(value: JsonValue | None) -> Connection:
    data = _object(value, "connection")
    raw_secret_references = data.get("secret_references")
    if not isinstance(raw_secret_references, list):
        raise ValueError("secret_references must be an array")
    secret_references = tuple(_secret_reference_from_json(item) for item in raw_secret_references)
    return Connection(
        id=_string(data, "id"),
        connector_type_id=_string(data, "connector_type_id"),
        connector_version=_string(data, "connector_version"),
        owner_type=_string(data, "owner_type"),
        owner_id=_string(data, "owner_id"),
        display_name=_string(data, "display_name"),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        organization_id=_optional_string(data.get("organization_id"), "organization_id"),
        endpoint_metadata=_object(data.get("endpoint_metadata"), "endpoint_metadata"),
        secret_references=secret_references,
        requested_scopes=_string_tuple(data.get("requested_scopes"), "requested_scopes"),
        granted_scopes=(),
        enabled=False,
        status=ConnectionStatus.DISABLED,
        health=HealthStatus.UNAVAILABLE,
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        last_checked_at=None,
        revision=_positive_int(data.get("revision"), "revision"),
        adapter_metadata=(),
    )


def _secret_reference_from_json(value: object) -> SecretReference:
    data = _object(value, "secret_reference")
    return SecretReference(
        provider=_string(data, "provider"),
        secret_id=_string(data, "secret_id"),
        scope=_string(data, "scope"),
        version=_optional_string(data.get("version"), "secret_reference.version"),
        metadata=_object(data.get("metadata"), "secret_reference.metadata"),
    )


def _require_snapshot(value: object) -> ConnectionPortableSnapshot:
    if not isinstance(value, ConnectionPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Connection portable codec requires a ConnectionPortableSnapshot",
        )
    return value


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(value: JsonValue | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string or null")
    return value


def _string_tuple(value: JsonValue | None, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field_name} must contain non-blank strings")
    items = tuple(cast(str, item) for item in value)
    if len(items) != len(set(items)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return items


def _bool_value(value: JsonValue | None, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _timestamp(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _positive_int(value: JsonValue | None, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value
