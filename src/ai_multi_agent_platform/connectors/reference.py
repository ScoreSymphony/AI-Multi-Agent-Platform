"""Deterministic self-hosted connector used for contracts and baseline development."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.configuration.secrets import SecretAccessContext, SecretProvider
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.domain import new_id

from .models import (
    Connection,
    ConnectionStatus,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorDefinition,
    ConnectorEvent,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalNativeReference,
    ExternalResourceReference,
    SyncCheckpoint,
    SyncMode,
    SyncStatus,
    connector_definition_id,
)
from .provider import ConnectorProvider

REFERENCE_CONNECTOR_TYPE = "reference.local"
REFERENCE_CONNECTOR_VERSION = "1.0"
REFERENCE_ACTION = "connector.reference.echo"


class ReferenceConnectorProvider(ConnectorProvider):
    """No-network fixture demonstrating credentials, resources, actions, events and sync."""

    def __init__(
        self,
        secret_provider: SecretProvider | None = None,
        *,
        provider_id: str = "connector.reference",
    ) -> None:
        self._secret_provider = secret_provider
        self._provider_id = provider_id
        self._health = HealthStatus.HEALTHY
        self._validated_connections: set[str] = set()
        self._records: dict[str, dict[str, str]] = {
            "alpha": {"value": "A"},
            "beta": {"value": "B"},
        }
        self._resource_ids = {native_id: new_id("external_resource") for native_id in self._records}
        self._event_ids = {native_id: new_id("connector_event") for native_id in self._records}

    @property
    def definition(self) -> ConnectorDefinition:
        return ConnectorDefinition(
            id=connector_definition_id(REFERENCE_CONNECTOR_TYPE, REFERENCE_CONNECTOR_VERSION),
            connector_type_id=REFERENCE_CONNECTOR_TYPE,
            name="Reference Local Connector",
            version=REFERENCE_CONNECTOR_VERSION,
            description="Deterministic local connector fixture with no external service.",
            supported_operations=(
                "validate",
                "health",
                "resource.list",
                "resource.read",
                "action.invoke",
                "sync",
                "sync.incremental",
                "sync.resync",
                "sync.rebuild",
            ),
            features=("resources", "actions", "events", "sync", "resync", "rebuild"),
            authentication_requirements=("token",),
            resource_types=("record",),
            actions=(REFERENCE_ACTION,),
            event_types=("record.changed",),
            configuration_schema={
                "type": "object",
                "properties": {"endpoint": {"type": "string"}},
                "additionalProperties": True,
            },
            health_semantics={
                "healthy": "provider fixture available",
                "unavailable": "provider fixture disabled for failure testing",
            },
            adapter_metadata=(
                AdapterMetadata(
                    namespace="platform.reference",
                    values={"source": "bundled", "provider_id": self._provider_id},
                ),
            ),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="connector",
            supported_operations=self.definition.supported_operations,
            capabilities=(
                Capability(
                    name=REFERENCE_ACTION,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=self._health,
            available=self._health is not HealthStatus.UNAVAILABLE,
        )

    def set_health(self, health: HealthStatus) -> None:
        self._health = health

    async def validate_connection(
        self, connection: Connection, context: OperationContext
    ) -> Connection:
        if (
            connection.connector_type_id != REFERENCE_CONNECTOR_TYPE
            or connection.connector_version != REFERENCE_CONNECTOR_VERSION
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "connection does not target the reference connector",
                provider_id=self._provider_id,
            )
        if not connection.secret_references:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "reference connector requires one secret reference",
                provider_id=self._provider_id,
            )
        if self._secret_provider is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "reference connector has no secret provider configured",
                provider_id=self._provider_id,
            )
        reference = connection.secret_references[0]
        await self._secret_provider.resolve(
            reference,
            SecretAccessContext(
                consumer_ref=self._provider_id,
                project_id=connection.project_id,
                action="connector.validate",
                purpose="connector-auth",
            ),
        )
        self._validated_connections.add(connection.id)
        now = datetime.now(UTC)
        adapter_metadata = tuple(
            metadata
            for metadata in connection.adapter_metadata
            if metadata.namespace != REFERENCE_CONNECTOR_TYPE
        ) + (
            AdapterMetadata(
                namespace=REFERENCE_CONNECTOR_TYPE,
                values={"account_id": "local-fixture"},
            ),
        )
        return replace(
            connection,
            status=(
                ConnectionStatus.READY
                if self._health is HealthStatus.HEALTHY
                else ConnectionStatus.DEGRADED
            ),
            health=self._health,
            granted_scopes=connection.requested_scopes,
            last_checked_at=now,
            updated_at=now,
            adapter_metadata=adapter_metadata,
        )

    async def connection_health(
        self, connection: Connection, context: OperationContext
    ) -> HealthStatus:
        self._require_connection(connection.id)
        return self._health

    async def list_resources(
        self, query: ConnectorResourceQuery
    ) -> tuple[ExternalResourceReference, ...]:
        self._require_connection(query.connection_id)
        self._require_available()
        if query.resource_type != "record":
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"reference connector does not expose {query.resource_type!r}",
                provider_id=self._provider_id,
            )
        prefix_raw = query.query.get("prefix")
        prefix = prefix_raw if isinstance(prefix_raw, str) else ""
        return tuple(
            self._resource(query.connection_id, native_id)
            for native_id in sorted(self._records)
            if native_id.startswith(prefix)
        )

    async def read_resource(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> ExternalResourceReference:
        self._require_connection(connection.id)
        self._require_available()
        native_id = resource.native_reference.native_id
        if native_id not in self._records:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"reference resource not found: {native_id}",
                provider_id=self._provider_id,
            )
        return self._resource(connection.id, native_id)

    async def invoke_action(self, invocation: ConnectorActionInvocation) -> ConnectorActionResult:
        self._require_connection(invocation.connection_id)
        self._require_available()
        if invocation.action != REFERENCE_ACTION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"reference connector does not expose action {invocation.action!r}",
                provider_id=self._provider_id,
            )
        return ConnectorActionResult(
            invocation_id=invocation.invocation_id,
            output={
                "echo": invocation.arguments.get("message"),
                "connection_id": invocation.connection_id,
            },
        )

    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        self._require_connection(request.connection_id)
        self._require_available()
        start = 0
        if (
            request.mode is SyncMode.INCREMENTAL
            and request.checkpoint is not None
            and request.checkpoint.cursor is not None
        ):
            try:
                start = int(request.checkpoint.cursor)
            except ValueError as exc:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "reference sync cursor is invalid",
                    provider_id=self._provider_id,
                ) from exc
        native_ids = sorted(self._records)
        if start < 0 or start > len(native_ids):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "reference sync cursor is outside the available range",
                provider_id=self._provider_id,
            )
        selected = native_ids[start:]
        now = datetime.now(UTC)
        resources = tuple(
            self._resource(request.connection_id, native_id) for native_id in selected
        )
        events = tuple(
            ConnectorEvent(
                id=self._event_ids[native_id],
                connector_type_id=REFERENCE_CONNECTOR_TYPE,
                connection_id=request.connection_id,
                event_type="record.changed",
                native_reference=ExternalNativeReference(
                    namespace=REFERENCE_CONNECTOR_TYPE,
                    native_id=native_id,
                ),
                schema_version="1.0",
                dedupe_key=f"reference.record:{native_id}:v1",
                received_at=now,
                project_id=request.context.project_id,
                resource_id=self._resource_ids[native_id],
                verified=True,
                provenance={
                    "source": "reference-local",
                    "revision": "1",
                    "sync_mode": request.mode.value,
                },
                payload={"native_id": native_id},
            )
            for native_id in selected
        )
        checkpoint = SyncCheckpoint(
            connection_id=request.connection_id,
            stream=request.stream,
            cursor=str(len(native_ids)),
            last_successful_sync=now,
            remote_revision=f"r{len(native_ids)}",
            status=SyncStatus.SUCCEEDED,
            dedupe_mapping={native_id: self._event_ids[native_id] for native_id in native_ids},
            updated_at=now,
        )
        return ConnectorSyncResult(
            checkpoint=checkpoint,
            resources=resources,
            events=events,
        )

    def _resource(self, connection_id: str, native_id: str) -> ExternalResourceReference:
        record = self._records[native_id]
        return ExternalResourceReference(
            id=self._resource_ids[native_id],
            connection_id=connection_id,
            resource_type="record",
            native_reference=ExternalNativeReference(
                namespace=REFERENCE_CONNECTOR_TYPE,
                native_id=native_id,
            ),
            revision="1",
            provenance={"connector": REFERENCE_CONNECTOR_TYPE},
            metadata={"value": record["value"]},
        )

    def _require_connection(self, connection_id: str) -> None:
        if connection_id not in self._validated_connections:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "connection has not been validated by reference connector",
                provider_id=self._provider_id,
            )

    def _require_available(self) -> None:
        if self._health is HealthStatus.UNAVAILABLE:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "reference connector is unavailable",
                retryable=True,
                provider_id=self._provider_id,
            )
