"""SEP-2663 external MCP task bindings and adapter-local interoperability contracts.

MCP task handles are provider state. They never become canonical platform Task or Run
identity. This module owns the durable correlation needed by the MCP adapter to resume
polling, scope cancellation and preserve retry/idempotency semantics.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, ToolInvocation

MCP_TASKS_EXTENSION_ID = "io.modelcontextprotocol/tasks"
MCP_TASKS_PROTOCOL_REVISION = "2026-07-28"
MCP_TASKS_SEP = "SEP-2663"
MCP_TASKS_SEP_REVISION = "9b44c6b4dcd2451bc49abd39e47eda36b396e8dd"

MCP_TASK_WORKING = "working"
MCP_TASK_INPUT_REQUIRED = "input_required"
MCP_TASK_COMPLETED = "completed"
MCP_TASK_CANCELLED = "cancelled"
MCP_TASK_FAILED = "failed"
MCP_TASK_TERMINAL_STATUSES = frozenset({MCP_TASK_COMPLETED, MCP_TASK_CANCELLED, MCP_TASK_FAILED})
MCP_TASK_KNOWN_STATUSES = frozenset(
    {
        MCP_TASK_WORKING,
        MCP_TASK_INPUT_REQUIRED,
        MCP_TASK_COMPLETED,
        MCP_TASK_CANCELLED,
        MCP_TASK_FAILED,
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MCPTaskBinding:
    """Durable external-task evidence for exactly one canonical invocation attempt."""

    invocation_id: str
    server_id: str
    tool_ref: str
    external_task_id: str
    correlation_id: str
    protocol_revision: str = MCP_TASKS_PROTOCOL_REVISION
    sep_revision: str = MCP_TASKS_SEP_REVISION
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    idempotency_key: str | None = None
    external_created_at: str | None = None
    latest_status: str = MCP_TASK_WORKING
    last_observed_at: datetime = field(default_factory=_utc_now)
    cancellation_requested_at: datetime | None = None
    cancellation_outcome: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "invocation_id",
            "server_id",
            "tool_ref",
            "external_task_id",
            "correlation_id",
            "protocol_revision",
            "sep_revision",
            "latest_status",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.last_observed_at.tzinfo is None:
            raise ValueError("last_observed_at must be timezone-aware")
        if (
            self.cancellation_requested_at is not None
            and self.cancellation_requested_at.tzinfo is None
        ):
            raise ValueError("cancellation_requested_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MCPInvocationOutcome:
    """Adapter-private final output plus evidence metadata."""

    output: JsonValue
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@runtime_checkable
class MCPInvocationClient(Protocol):
    """Optional richer MCP client seam for invocation-scoped asynchronous execution."""

    async def call_tool_for_invocation(
        self, invocation: ToolInvocation
    ) -> MCPInvocationOutcome: ...


@runtime_checkable
class MCPInvocationMetadataProvider(Protocol):
    """Expose adapter evidence when canonical timeout/cancellation owns final disposition."""

    def invocation_failure_metadata(
        self,
        invocation: ToolInvocation,
    ) -> tuple[AdapterMetadata, ...]: ...


class MCPTaskObserver(Protocol):
    """Telemetry-only task observation sink; observations never mutate canonical lifecycle."""

    async def record_task_observation(self, binding: MCPTaskBinding) -> None: ...


class NullMCPTaskObserver:
    async def record_task_observation(self, binding: MCPTaskBinding) -> None:
        return None


class MCPTaskBindingRepository(Protocol):
    """Persistence seam keyed by server + canonical invocation attempt."""

    async def get(self, server_id: str, invocation_id: str) -> MCPTaskBinding | None: ...

    async def put_if_absent(self, binding: MCPTaskBinding) -> MCPTaskBinding: ...

    async def update(self, binding: MCPTaskBinding) -> None: ...


class InMemoryMCPTaskBindingRepository:
    """Deterministic test/local binding store."""

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], MCPTaskBinding] = {}
        self._lock = asyncio.Lock()

    async def get(self, server_id: str, invocation_id: str) -> MCPTaskBinding | None:
        async with self._lock:
            return self._bindings.get((server_id, invocation_id))

    async def put_if_absent(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        key = (binding.server_id, binding.invocation_id)
        async with self._lock:
            current = self._bindings.get(key)
            if current is None:
                self._bindings[key] = binding
                return binding
            _require_same_binding(current, binding)
            return current

    async def update(self, binding: MCPTaskBinding) -> None:
        key = (binding.server_id, binding.invocation_id)
        async with self._lock:
            current = self._bindings.get(key)
            if current is None:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "MCP external task binding does not exist",
                    provider_id=f"mcp:{binding.server_id}",
                )
            _require_same_binding(current, binding)
            self._bindings[key] = binding


class SqliteMCPTaskBindingRepository:
    """Restart-durable binding store with runtime SQLite work offloaded from the event loop."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS mcp_task_bindings (
        server_id TEXT NOT NULL,
        invocation_id TEXT NOT NULL,
        tool_ref TEXT NOT NULL,
        external_task_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        protocol_revision TEXT NOT NULL,
        sep_revision TEXT NOT NULL,
        task_id TEXT,
        run_id TEXT,
        agent_id TEXT,
        idempotency_key TEXT,
        external_created_at TEXT,
        latest_status TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        cancellation_requested_at TEXT,
        cancellation_outcome TEXT,
        PRIMARY KEY (server_id, invocation_id),
        UNIQUE (server_id, external_task_id)
    )
    """

    def __init__(self, database_path: str | Path, *, max_concurrency: int = 4) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        with self._connect() as connection:
            connection.execute(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    async def _run[T](self, operation: Callable[[], T]) -> T:
        async with self._semaphore:
            try:
                return await asyncio.to_thread(operation)
            except ContractError:
                raise
            except sqlite3.IntegrityError as exc:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "MCP external task binding conflicts with persisted evidence",
                ) from exc
            except sqlite3.Error as exc:
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    "MCP external task binding persistence failed",
                    retryable=True,
                ) from exc

    async def get(self, server_id: str, invocation_id: str) -> MCPTaskBinding | None:
        def operation() -> MCPTaskBinding | None:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM mcp_task_bindings
                    WHERE server_id = ? AND invocation_id = ?
                    """,
                    (server_id, invocation_id),
                ).fetchone()
            return None if row is None else _binding_from_row(row)

        return await self._run(operation)

    async def put_if_absent(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        def operation() -> MCPTaskBinding:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM mcp_task_bindings
                    WHERE server_id = ? AND invocation_id = ?
                    """,
                    (binding.server_id, binding.invocation_id),
                ).fetchone()
                if row is not None:
                    current = _binding_from_row(row)
                    _require_same_binding(current, binding)
                    return current
                connection.execute(
                    """
                    INSERT INTO mcp_task_bindings (
                        server_id, invocation_id, tool_ref, external_task_id, correlation_id,
                        protocol_revision, sep_revision, task_id, run_id, agent_id,
                        idempotency_key, external_created_at, latest_status, last_observed_at,
                        cancellation_requested_at, cancellation_outcome
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _binding_values(binding),
                )
            return binding

        return await self._run(operation)

    async def update(self, binding: MCPTaskBinding) -> None:
        def operation() -> None:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM mcp_task_bindings
                    WHERE server_id = ? AND invocation_id = ?
                    """,
                    (binding.server_id, binding.invocation_id),
                ).fetchone()
                if row is None:
                    raise ContractError(
                        ErrorCode.NOT_FOUND,
                        "MCP external task binding does not exist",
                        provider_id=f"mcp:{binding.server_id}",
                    )
                current = _binding_from_row(row)
                _require_same_binding(current, binding)
                connection.execute(
                    """
                    UPDATE mcp_task_bindings
                    SET latest_status = ?,
                        last_observed_at = ?,
                        cancellation_requested_at = ?,
                        cancellation_outcome = ?
                    WHERE server_id = ? AND invocation_id = ?
                    """,
                    (
                        binding.latest_status,
                        binding.last_observed_at.isoformat(),
                        _datetime_text(binding.cancellation_requested_at),
                        binding.cancellation_outcome,
                        binding.server_id,
                        binding.invocation_id,
                    ),
                )

        await self._run(operation)


def binding_for_invocation(
    invocation: ToolInvocation,
    *,
    server_id: str,
    external_task_id: str,
    external_status: str,
    external_created_at: str | None,
    protocol_revision: str,
) -> MCPTaskBinding:
    """Create external evidence without reusing provider identity as canonical identity."""

    return MCPTaskBinding(
        invocation_id=invocation.invocation_id,
        server_id=server_id,
        tool_ref=invocation.tool_ref,
        external_task_id=external_task_id,
        correlation_id=invocation.context.correlation_id,
        protocol_revision=protocol_revision,
        task_id=invocation.task_id,
        run_id=invocation.run_id,
        agent_id=invocation.agent_id,
        idempotency_key=invocation.context.control.idempotency_key,
        external_created_at=external_created_at,
        latest_status=external_status,
    )


def validate_binding_scope(binding: MCPTaskBinding, invocation: ToolInvocation) -> None:
    """Prevent task-ID knowledge from becoming authority over another invocation."""

    expected = (
        invocation.invocation_id,
        invocation.tool_ref,
        invocation.context.correlation_id,
        invocation.task_id,
        invocation.run_id,
        invocation.agent_id,
        invocation.context.control.idempotency_key,
    )
    actual = (
        binding.invocation_id,
        binding.tool_ref,
        binding.correlation_id,
        binding.task_id,
        binding.run_id,
        binding.agent_id,
        binding.idempotency_key,
    )
    if actual != expected:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "MCP external task binding does not match the authorized canonical invocation",
            provider_id=f"mcp:{binding.server_id}",
        )


def task_adapter_metadata(binding: MCPTaskBinding) -> tuple[AdapterMetadata, ...]:
    values: dict[str, JsonValue] = {
        "server_id": binding.server_id,
        "external_task_id": binding.external_task_id,
        "external_status": binding.latest_status,
        "protocol_revision": binding.protocol_revision,
        "extension": MCP_TASKS_EXTENSION_ID,
        "sep": MCP_TASKS_SEP,
        "sep_revision": binding.sep_revision,
    }
    if binding.cancellation_requested_at is not None:
        values["cancellation_requested"] = True
    if binding.cancellation_outcome is not None:
        values["cancellation_outcome"] = binding.cancellation_outcome
    return (AdapterMetadata(namespace="mcp.task", values=values),)


def observed_binding(binding: MCPTaskBinding, status: str) -> MCPTaskBinding:
    if status not in MCP_TASK_KNOWN_STATUSES:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"MCP Tasks returned unknown status {status!r}",
            provider_id=f"mcp:{binding.server_id}",
            details={"external_task_state_unknown": True},
            adapter_metadata=task_adapter_metadata(binding),
        )
    if binding.latest_status in MCP_TASK_TERMINAL_STATUSES and status != binding.latest_status:
        # A late/stale provider observation cannot rewrite terminal external evidence.
        return binding
    return replace(binding, latest_status=status, last_observed_at=_utc_now())


def cancellation_requested(binding: MCPTaskBinding) -> MCPTaskBinding:
    return replace(
        binding,
        cancellation_requested_at=binding.cancellation_requested_at or _utc_now(),
        cancellation_outcome=binding.cancellation_outcome,
    )


def cancellation_result(binding: MCPTaskBinding, outcome: str) -> MCPTaskBinding:
    if not outcome.strip():
        raise ValueError("cancellation outcome must not be blank")
    return replace(binding, cancellation_outcome=outcome, last_observed_at=_utc_now())


def _require_same_binding(current: MCPTaskBinding, candidate: MCPTaskBinding) -> None:
    immutable_fields = (
        "invocation_id",
        "server_id",
        "tool_ref",
        "external_task_id",
        "correlation_id",
        "protocol_revision",
        "sep_revision",
        "task_id",
        "run_id",
        "agent_id",
        "idempotency_key",
        "external_created_at",
    )
    if any(getattr(current, field) != getattr(candidate, field) for field in immutable_fields):
        raise ContractError(
            ErrorCode.CONFLICT,
            "one canonical invocation attempt cannot be rebound to another MCP task",
            provider_id=f"mcp:{current.server_id}",
            details={"mcp_task_binding_conflict": True},
        )


def _binding_values(binding: MCPTaskBinding) -> tuple[object, ...]:
    return (
        binding.server_id,
        binding.invocation_id,
        binding.tool_ref,
        binding.external_task_id,
        binding.correlation_id,
        binding.protocol_revision,
        binding.sep_revision,
        binding.task_id,
        binding.run_id,
        binding.agent_id,
        binding.idempotency_key,
        binding.external_created_at,
        binding.latest_status,
        binding.last_observed_at.isoformat(),
        _datetime_text(binding.cancellation_requested_at),
        binding.cancellation_outcome,
    )


def _binding_from_row(row: sqlite3.Row) -> MCPTaskBinding:
    requested = row["cancellation_requested_at"]
    return MCPTaskBinding(
        invocation_id=str(row["invocation_id"]),
        server_id=str(row["server_id"]),
        tool_ref=str(row["tool_ref"]),
        external_task_id=str(row["external_task_id"]),
        correlation_id=str(row["correlation_id"]),
        protocol_revision=str(row["protocol_revision"]),
        sep_revision=str(row["sep_revision"]),
        task_id=None if row["task_id"] is None else str(row["task_id"]),
        run_id=None if row["run_id"] is None else str(row["run_id"]),
        agent_id=None if row["agent_id"] is None else str(row["agent_id"]),
        idempotency_key=(None if row["idempotency_key"] is None else str(row["idempotency_key"])),
        external_created_at=(
            None if row["external_created_at"] is None else str(row["external_created_at"])
        ),
        latest_status=str(row["latest_status"]),
        last_observed_at=datetime.fromisoformat(str(row["last_observed_at"])),
        cancellation_requested_at=(
            None if requested is None else datetime.fromisoformat(str(requested))
        ),
        cancellation_outcome=(
            None if row["cancellation_outcome"] is None else str(row["cancellation_outcome"])
        ),
    )


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
