"""SEP-2663 MCP Tasks interoperability behind the canonical capability boundary.

MCP tasks are external provider state. They never replace the platform-owned Task,
Run or ToolInvocation identities that authorize and explain one capability attempt.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, ToolInvocation

MCP_TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
MCP_TASKS_PROTOCOL_REVISION = "2026-07-28"
MCP_TASKS_SEP = "SEP-2663"


class MCPTaskStatus(StrEnum):
    """Provider-native SEP-2663 task states retained only at the adapter boundary."""

    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {
            MCPTaskStatus.COMPLETED,
            MCPTaskStatus.CANCELLED,
            MCPTaskStatus.FAILED,
        }


@dataclass(frozen=True, slots=True)
class MCPTaskSnapshot:
    """One untrusted detailed observation of an external MCP task."""

    task_id: str
    status: MCPTaskStatus
    created_at: datetime
    last_updated_at: datetime
    ttl_ms: int | None
    poll_interval_ms: int | None = None
    status_message: str | None = None
    result: JsonValue = None
    error: JsonValue = None
    input_requests: dict[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("MCP task_id must not be blank")
        if self.created_at.tzinfo is None or self.last_updated_at.tzinfo is None:
            raise ValueError("MCP task timestamps must be timezone-aware")
        if self.ttl_ms is not None and self.ttl_ms < 0:
            raise ValueError("MCP task ttl_ms must not be negative")
        if self.poll_interval_ms is not None and self.poll_interval_ms < 0:
            raise ValueError("MCP task poll_interval_ms must not be negative")
        if self.status_message is not None and not self.status_message.strip():
            raise ValueError("MCP task status_message must not be blank")
        if self.status is MCPTaskStatus.COMPLETED and self.result is None:
            raise ValueError("completed MCP task must carry a result")
        if self.status is MCPTaskStatus.FAILED and self.error is None:
            raise ValueError("failed MCP task must carry an error")
        if self.status is MCPTaskStatus.INPUT_REQUIRED and not self.input_requests:
            raise ValueError("input_required MCP task must carry input_requests")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        object.__setattr__(self, "last_updated_at", self.last_updated_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class MCPImmediateToolResult:
    """A task-capable tools/call that still completed synchronously."""

    output: JsonValue


@dataclass(frozen=True, slots=True)
class MCPTaskStarted:
    """A server-directed asynchronous tools/call result."""

    task: MCPTaskSnapshot


type MCPTaskCallResult = MCPImmediateToolResult | MCPTaskStarted


@runtime_checkable
class MCPTaskClient(Protocol):
    """Optional SEP-2663 surface implemented by an MCP transport client."""

    @property
    def task_protocol_revision(self) -> str: ...

    async def supports_tasks(self) -> bool: ...

    async def call_tool_with_tasks(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> MCPTaskCallResult: ...

    async def get_task(self, task_id: str) -> MCPTaskSnapshot: ...

    async def update_task(
        self,
        task_id: str,
        input_responses: dict[str, JsonValue],
    ) -> None: ...

    async def cancel_task(self, task_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class MCPTaskBinding:
    """Durable binding from one canonical attempt to one external MCP task handle.

    The raw external task handle is kept in this protected adapter store because recovery needs
    it. Generic telemetry uses only a digest so a bearer-like MCP task ID is not copied into logs.
    """

    provider_id: str
    server_id: str
    invocation_id: str
    canonical_task_id: str
    canonical_run_id: str
    owner_type: str
    owner_id: str
    project_id: str | None
    causation_id: str | None
    correlation_id: str
    idempotency_key: str | None
    external_task_id: str
    external_created_at: datetime
    latest_status: MCPTaskStatus
    latest_observed_at: datetime
    protocol_revision: str
    extension_id: str = MCP_TASKS_EXTENSION_ID
    poll_interval_ms: int | None = None
    responded_input_keys: tuple[str, ...] = ()
    cancellation_requested_at: datetime | None = None
    cancellation_acknowledged: bool = False
    cancellation_error_code: str | None = None
    terminal_payload_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "provider_id",
            "server_id",
            "invocation_id",
            "canonical_task_id",
            "canonical_run_id",
            "owner_type",
            "owner_id",
            "correlation_id",
            "external_task_id",
            "protocol_revision",
            "extension_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        if any(not key.strip() for key in self.responded_input_keys):
            raise ValueError("responded_input_keys must not contain blank keys")
        if len(set(self.responded_input_keys)) != len(self.responded_input_keys):
            raise ValueError("responded_input_keys must not contain duplicates")
        for timestamp_name in (
            "external_created_at",
            "latest_observed_at",
            "cancellation_requested_at",
        ):
            value = getattr(self, timestamp_name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{timestamp_name} must be timezone-aware")
        if self.poll_interval_ms is not None and self.poll_interval_ms < 0:
            raise ValueError("poll_interval_ms must not be negative")
        if self.cancellation_error_code is not None and not self.cancellation_error_code.strip():
            raise ValueError("cancellation_error_code must not be blank")
        object.__setattr__(self, "external_created_at", self.external_created_at.astimezone(UTC))
        object.__setattr__(self, "latest_observed_at", self.latest_observed_at.astimezone(UTC))
        if self.cancellation_requested_at is not None:
            object.__setattr__(
                self,
                "cancellation_requested_at",
                self.cancellation_requested_at.astimezone(UTC),
            )

    @property
    def external_task_id_digest(self) -> str:
        return hashlib.sha256(self.external_task_id.encode("utf-8")).hexdigest()


class MCPTaskBindingStore(Protocol):
    """Persistence seam for recovery without turning MCP state into canonical state."""

    async def get(self, provider_id: str, invocation_id: str) -> MCPTaskBinding | None: ...

    async def bind(self, binding: MCPTaskBinding) -> MCPTaskBinding: ...

    async def save(self, binding: MCPTaskBinding) -> MCPTaskBinding: ...


class InMemoryMCPTaskBindingStore:
    """Deterministic local/reference binding store."""

    def __init__(self) -> None:
        self._by_invocation: dict[tuple[str, str], MCPTaskBinding] = {}
        self._by_external: dict[tuple[str, str], tuple[str, str]] = {}

    async def get(self, provider_id: str, invocation_id: str) -> MCPTaskBinding | None:
        return self._by_invocation.get((provider_id, invocation_id))

    async def bind(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        key = (binding.provider_id, binding.invocation_id)
        existing = self._by_invocation.get(key)
        if existing is not None:
            _assert_same_binding_identity(existing, binding)
            return existing
        external_key = (binding.provider_id, binding.external_task_id)
        owner = self._by_external.get(external_key)
        if owner is not None and owner != key:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external MCP task handle is already bound to another canonical invocation",
                provider_id=binding.provider_id,
            )
        self._by_invocation[key] = binding
        self._by_external[external_key] = key
        return binding

    async def save(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        key = (binding.provider_id, binding.invocation_id)
        existing = self._by_invocation.get(key)
        if existing is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "MCP task binding does not exist",
                provider_id=binding.provider_id,
            )
        merged = _merge_binding_observation(existing, binding)
        self._by_invocation[key] = merged
        return merged


class SqliteMCPTaskBindingStore:
    """Restart-safe stdlib SQLite reference store for external MCP task bindings."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mcp_task_bindings (
                        provider_id TEXT NOT NULL,
                        invocation_id TEXT NOT NULL,
                        external_task_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(provider_id, invocation_id),
                        UNIQUE(provider_id, external_task_id)
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize MCP task binding storage",
            ) from exc

    async def get(self, provider_id: str, invocation_id: str) -> MCPTaskBinding | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload FROM mcp_task_bindings
                    WHERE provider_id = ? AND invocation_id = ?
                    """,
                    (provider_id, invocation_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read MCP task binding",
                provider_id=provider_id,
            ) from exc
        if row is None:
            return None
        return _decode_binding(str(row["payload"]))

    async def bind(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        existing = await self.get(binding.provider_id, binding.invocation_id)
        if existing is not None:
            _assert_same_binding_identity(existing, binding)
            return existing
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO mcp_task_bindings(
                        provider_id, invocation_id, external_task_id, payload
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        binding.provider_id,
                        binding.invocation_id,
                        binding.external_task_id,
                        _encode_binding(binding),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            existing = await self.get(binding.provider_id, binding.invocation_id)
            if existing is not None:
                _assert_same_binding_identity(existing, binding)
                return existing
            raise ContractError(
                ErrorCode.CONFLICT,
                "external MCP task handle is already bound to another canonical invocation",
                provider_id=binding.provider_id,
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist MCP task binding",
                provider_id=binding.provider_id,
            ) from exc
        return binding

    async def save(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        existing = await self.get(binding.provider_id, binding.invocation_id)
        if existing is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "MCP task binding does not exist",
                provider_id=binding.provider_id,
            )
        merged = _merge_binding_observation(existing, binding)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE mcp_task_bindings SET payload = ?
                    WHERE provider_id = ? AND invocation_id = ?
                    """,
                    (
                        _encode_binding(merged),
                        merged.provider_id,
                        merged.invocation_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to update MCP task binding",
                provider_id=binding.provider_id,
            ) from exc
        return merged


def binding_for_snapshot(
    invocation: ToolInvocation,
    *,
    provider_id: str,
    server_id: str,
    protocol_revision: str,
    snapshot: MCPTaskSnapshot,
) -> MCPTaskBinding:
    """Bind a server task to the exact canonical attempt that caused it."""

    if invocation.task_id is None or invocation.run_id is None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "MCP Tasks require canonical task_id and run_id trace context",
            provider_id=provider_id,
        )
    if invocation.context.owner_type is None or invocation.context.owner_id is None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "MCP Tasks require an authenticated canonical owner context",
            provider_id=provider_id,
        )
    return MCPTaskBinding(
        provider_id=provider_id,
        server_id=server_id,
        invocation_id=invocation.invocation_id,
        canonical_task_id=invocation.task_id,
        canonical_run_id=invocation.run_id,
        owner_type=invocation.context.owner_type,
        owner_id=invocation.context.owner_id,
        project_id=invocation.context.project_id,
        causation_id=invocation.context.causation_id,
        correlation_id=invocation.context.correlation_id,
        idempotency_key=invocation.context.control.idempotency_key,
        external_task_id=snapshot.task_id,
        external_created_at=snapshot.created_at,
        latest_status=snapshot.status,
        latest_observed_at=snapshot.last_updated_at,
        protocol_revision=protocol_revision,
        poll_interval_ms=snapshot.poll_interval_ms,
        terminal_payload_digest=_terminal_payload_digest(snapshot),
    )


def observe_snapshot(binding: MCPTaskBinding, snapshot: MCPTaskSnapshot) -> MCPTaskBinding:
    """Create a monotonic candidate observation without trusting provider lifecycle authority."""

    if snapshot.task_id != binding.external_task_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "MCP task observation does not match the bound external task handle",
            provider_id=binding.provider_id,
        )
    return replace(
        binding,
        latest_status=snapshot.status,
        latest_observed_at=snapshot.last_updated_at,
        poll_interval_ms=snapshot.poll_interval_ms,
        terminal_payload_digest=_terminal_payload_digest(snapshot),
    )


def validate_binding_for_invocation(binding: MCPTaskBinding, invocation: ToolInvocation) -> None:
    """Prevent a known external handle from being reused under another actor/Run/capability call."""

    expected = (
        invocation.invocation_id,
        invocation.task_id,
        invocation.run_id,
        invocation.context.owner_type,
        invocation.context.owner_id,
        invocation.context.project_id,
        invocation.context.causation_id,
        invocation.context.correlation_id,
        invocation.context.control.idempotency_key,
    )
    actual = (
        binding.invocation_id,
        binding.canonical_task_id,
        binding.canonical_run_id,
        binding.owner_type,
        binding.owner_id,
        binding.project_id,
        binding.causation_id,
        binding.correlation_id,
        binding.idempotency_key,
    )
    if expected != actual:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "MCP task binding cannot be reused outside its canonical invocation context",
            provider_id=binding.provider_id,
        )


def binding_adapter_metadata(binding: MCPTaskBinding) -> tuple[AdapterMetadata, ...]:
    """Safe telemetry projection; raw bearer-like task IDs stay in the protected binding store."""

    return (
        AdapterMetadata(
            namespace="mcp.tasks",
            values={
                "server_id": binding.server_id,
                "extension_id": binding.extension_id,
                "protocol_revision": binding.protocol_revision,
                "external_task_id_sha256": binding.external_task_id_digest,
                "external_status": binding.latest_status.value,
                "correlation_id": binding.correlation_id,
                "responded_input_request_count": len(binding.responded_input_keys),
                "cancellation_requested": binding.cancellation_requested_at is not None,
                "cancellation_acknowledged": binding.cancellation_acknowledged,
                "cancellation_error_code": binding.cancellation_error_code,
            },
        ),
    )


def mark_input_requests_responded(
    binding: MCPTaskBinding,
    keys: tuple[str, ...],
) -> MCPTaskBinding:
    """Persist input-request deduplication only after tasks/update succeeds."""

    if not keys:
        return binding
    if any(not key.strip() for key in keys):
        raise ValueError("MCP input response keys must not be blank")
    merged = tuple(dict.fromkeys((*binding.responded_input_keys, *keys)))
    return replace(binding, responded_input_keys=merged)


def mark_cancellation_requested(binding: MCPTaskBinding) -> MCPTaskBinding:
    if binding.cancellation_requested_at is not None:
        return binding
    return replace(binding, cancellation_requested_at=datetime.now(UTC))


def mark_cancellation_result(
    binding: MCPTaskBinding,
    *,
    acknowledged: bool,
    error_code: str | None = None,
) -> MCPTaskBinding:
    return replace(
        binding,
        cancellation_acknowledged=binding.cancellation_acknowledged or acknowledged,
        cancellation_error_code=error_code,
    )


def _terminal_payload_digest(snapshot: MCPTaskSnapshot) -> str | None:
    if not snapshot.status.terminal:
        return None
    payload = snapshot.result if snapshot.status is MCPTaskStatus.COMPLETED else snapshot.error
    if payload is None:
        payload = {"status": snapshot.status.value}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _assert_same_binding_identity(existing: MCPTaskBinding, candidate: MCPTaskBinding) -> None:
    identity_fields = (
        "provider_id",
        "server_id",
        "invocation_id",
        "canonical_task_id",
        "canonical_run_id",
        "owner_type",
        "owner_id",
        "project_id",
        "causation_id",
        "correlation_id",
        "idempotency_key",
        "external_task_id",
        "protocol_revision",
        "extension_id",
    )
    if any(getattr(existing, name) != getattr(candidate, name) for name in identity_fields):
        raise ContractError(
            ErrorCode.CONFLICT,
            "canonical invocation already has a different MCP task binding",
            provider_id=existing.provider_id,
        )


def _merge_binding_observation(
    existing: MCPTaskBinding,
    candidate: MCPTaskBinding,
) -> MCPTaskBinding:
    _assert_same_binding_identity(existing, candidate)
    if candidate.latest_observed_at < existing.latest_observed_at:
        return existing
    if candidate.latest_observed_at == existing.latest_observed_at:
        if candidate.latest_status is not existing.latest_status:
            raise ContractError(
                ErrorCode.CONFLICT,
                "MCP task returned conflicting states for the same observation timestamp",
                provider_id=existing.provider_id,
            )
    if existing.latest_status.terminal and candidate.latest_status is not existing.latest_status:
        return existing
    return replace(
        candidate,
        responded_input_keys=tuple(
            dict.fromkeys((*existing.responded_input_keys, *candidate.responded_input_keys))
        ),
        cancellation_requested_at=(
            existing.cancellation_requested_at or candidate.cancellation_requested_at
        ),
        cancellation_acknowledged=(
            existing.cancellation_acknowledged or candidate.cancellation_acknowledged
        ),
        cancellation_error_code=(
            candidate.cancellation_error_code or existing.cancellation_error_code
        ),
        terminal_payload_digest=(
            candidate.terminal_payload_digest or existing.terminal_payload_digest
        ),
    )


def _encode_binding(binding: MCPTaskBinding) -> str:
    payload = asdict(binding)
    payload["latest_status"] = binding.latest_status.value
    for key in ("external_created_at", "latest_observed_at", "cancellation_requested_at"):
        value = payload[key]
        if isinstance(value, datetime):
            payload[key] = value.astimezone(UTC).isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decode_binding(payload: str) -> MCPTaskBinding:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise TypeError("binding payload must be an object")
        cancellation = data.get("cancellation_requested_at")
        responded_input_keys_value = data.get("responded_input_keys", [])
        if not isinstance(responded_input_keys_value, list) or not all(
            isinstance(item, str) for item in responded_input_keys_value
        ):
            raise TypeError("responded_input_keys must be a string array")
        return MCPTaskBinding(
            provider_id=str(data["provider_id"]),
            server_id=str(data["server_id"]),
            invocation_id=str(data["invocation_id"]),
            canonical_task_id=str(data["canonical_task_id"]),
            canonical_run_id=str(data["canonical_run_id"]),
            owner_type=str(data["owner_type"]),
            owner_id=str(data["owner_id"]),
            project_id=(None if data.get("project_id") is None else str(data["project_id"])),
            causation_id=(None if data.get("causation_id") is None else str(data["causation_id"])),
            correlation_id=str(data["correlation_id"]),
            idempotency_key=(
                None if data.get("idempotency_key") is None else str(data["idempotency_key"])
            ),
            external_task_id=str(data["external_task_id"]),
            external_created_at=datetime.fromisoformat(str(data["external_created_at"])),
            latest_status=MCPTaskStatus(str(data["latest_status"])),
            latest_observed_at=datetime.fromisoformat(str(data["latest_observed_at"])),
            protocol_revision=str(data["protocol_revision"]),
            extension_id=str(data.get("extension_id", MCP_TASKS_EXTENSION_ID)),
            poll_interval_ms=(
                None if data.get("poll_interval_ms") is None else int(data["poll_interval_ms"])
            ),
            responded_input_keys=tuple(responded_input_keys_value),
            cancellation_requested_at=(
                None if cancellation is None else datetime.fromisoformat(str(cancellation))
            ),
            cancellation_acknowledged=bool(data.get("cancellation_acknowledged", False)),
            cancellation_error_code=(
                None
                if data.get("cancellation_error_code") is None
                else str(data["cancellation_error_code"])
            ),
            terminal_payload_digest=(
                None
                if data.get("terminal_payload_digest") is None
                else str(data["terminal_payload_digest"])
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "persisted MCP task binding is invalid",
        ) from exc
