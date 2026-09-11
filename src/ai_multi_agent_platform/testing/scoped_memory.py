"""Refined scoped-memory conformance helpers and deterministic in-memory provider."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.data import (
    DataAccessContext,
    MemoryEntry,
    MemoryOrigin,
    MemoryProvider,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id

from .conformance import assert_provider_contract

_REFINED_MEMORY_OPERATIONS = (
    "write",
    "get",
    "query",
    "search",
    "supersede",
    "delete",
    "expire",
)
_EXACT_EXPIRY_OPERATION = "expire_entry"
_DISCOVERY_OPERATION = "list_entries_for_discovery"


def _entry_signature(entry: MemoryEntry) -> tuple[object, ...]:
    return (
        entry.memory_id,
        entry.scope,
        entry.scope_id,
        entry.owner_ref,
        entry.created_by,
        entry.value,
        entry.created_at,
        entry.retention,
        entry.origin,
        entry.expires_at,
        entry.provenance,
        entry.classification,
        entry.metadata,
        entry.memory_type,
    )


def _assert_canonical_type(entry: MemoryEntry, expected: MemoryType) -> None:
    if not isinstance(entry.memory_type, MemoryType):
        raise AssertionError("memory provider leaked a provider-private type identifier")
    if entry.memory_type is not expected:
        raise AssertionError(
            f"memory provider changed canonical memory_type from {expected.value} "
            f"to {entry.memory_type.value}"
        )
    private_type = entry.metadata.get("provider_private_type")
    if isinstance(private_type, str) and entry.memory_type.value == private_type:
        raise AssertionError("provider-private taxonomy replaced canonical memory_type")


async def _assert_not_found(
    provider: MemoryProvider,
    memory_id: str,
    context: DataAccessContext,
) -> None:
    try:
        await provider.get_entry(memory_id, context)
    except ContractError as error:
        if error.code is not ErrorCode.NOT_FOUND:
            raise AssertionError("removed memory must fail with canonical not_found") from error
        return
    raise AssertionError("removed memory remained readable")


async def assert_scoped_memory_provider_contract(
    provider: MemoryProvider,
    context: DataAccessContext,
) -> None:
    """Verify refined ``MemoryProvider`` lifecycle and canonical ``MemoryType`` semantics.

    This helper is intentionally backend-neutral. It exercises only canonical data models
    and provider operations; adapters remain free to use any private storage, taxonomy or
    retrieval implementation behind the boundary.
    """

    await assert_provider_contract(provider)
    declared = set(provider.descriptor.supported_operations)
    missing = set(_REFINED_MEMORY_OPERATIONS) - declared
    if missing:
        names = ", ".join(sorted(missing))
        raise AssertionError(f"refined memory provider must declare baseline operations: {names}")

    scope_id = new_id("task")
    created_at = datetime.now(UTC) - timedelta(minutes=10)
    originals: dict[MemoryType, MemoryEntry] = {}

    for memory_type in MemoryType:
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.TASK,
            scope_id=scope_id,
            owner_ref=context.actor_ref,
            created_by=context.actor_ref,
            value={"text": f"shared-memory-type-marker {memory_type.value}"},
            created_at=created_at,
            retention=RetentionPolicy.TASK_LIFETIME,
            origin=MemoryOrigin.IMPORTED,
            provenance=(SourceRef(kind="conformance", ref=f"source:{memory_type.value}"),),
            metadata={"provider_private_type": f"native::{memory_type.value}"},
            memory_type=memory_type,
        )
        stored = await provider.write_entry(entry, context)
        _assert_canonical_type(stored, memory_type)
        if _entry_signature(stored) != _entry_signature(entry):
            raise AssertionError("memory write round-trip changed canonical entry dimensions")

        restored = await provider.get_entry(entry.memory_id, context)
        _assert_canonical_type(restored, memory_type)
        if _entry_signature(restored) != _entry_signature(entry):
            raise AssertionError("memory read round-trip changed canonical entry dimensions")
        originals[memory_type] = entry

    scope_only = await provider.query_entries(
        MemoryQuery(MemoryScope.TASK, scope_id, memory_types=()),
        context,
    )
    if {entry.memory_id for entry in scope_only} != {
        entry.memory_id for entry in originals.values()
    }:
        raise AssertionError("scope-only query must remain valid when memory_types is empty")

    exact_type = MemoryType.PREFERENCE
    exact = await provider.query_entries(
        MemoryQuery(MemoryScope.TASK, scope_id, memory_types=(exact_type,)),
        context,
    )
    if {entry.memory_id for entry in exact} != {originals[exact_type].memory_id}:
        raise AssertionError(
            "exact memory_type filtering returned entries outside the requested type"
        )
    for entry in exact:
        _assert_canonical_type(entry, exact_type)

    multi_types = (MemoryType.SEMANTIC, MemoryType.REFLECTIVE)
    multi = await provider.query_entries(
        MemoryQuery(MemoryScope.TASK, scope_id, memory_types=multi_types),
        context,
    )
    expected_multi = {originals[memory_type].memory_id for memory_type in multi_types}
    if {entry.memory_id for entry in multi} != expected_multi:
        raise AssertionError("multi-type filtering returned the wrong canonical type set")
    if {entry.memory_type for entry in multi} != set(multi_types):
        raise AssertionError("multi-type filtering collapsed canonical memory types")

    search_type = MemoryType.PROCEDURAL
    search_hits = await provider.search_entries(
        MemoryQuery(MemoryScope.TASK, scope_id, memory_types=(search_type,)),
        "shared-memory-type-marker",
        context,
    )
    if {entry.memory_id for entry in search_hits} != {originals[search_type].memory_id}:
        raise AssertionError("memory search must honor MemoryQuery.memory_types filtering")
    for entry in search_hits:
        _assert_canonical_type(entry, search_type)

    source = originals[MemoryType.EPISODIC]
    replacement = MemoryEntry(
        memory_id=new_memory_id(),
        scope=source.scope,
        scope_id=source.scope_id,
        owner_ref=source.owner_ref,
        created_by=source.created_by,
        value={"text": "replacement reflective memory"},
        created_at=datetime.now(UTC),
        retention=source.retention,
        origin=MemoryOrigin.AGENT_DERIVED,
        provenance=(SourceRef(kind="memory", ref=source.memory_id),),
        metadata={"provider_private_type": "native::reflection"},
        memory_type=MemoryType.REFLECTIVE,
    )
    linked = await provider.supersede_entry(source.memory_id, replacement, context)
    _assert_canonical_type(linked, MemoryType.REFLECTIVE)
    if linked.supersedes_memory_id != source.memory_id:
        raise AssertionError("replacement memory must link to the superseded canonical memory_id")

    historical_source = await provider.query_entries(
        MemoryQuery(
            MemoryScope.TASK,
            scope_id,
            include_superseded=True,
            memory_types=(MemoryType.EPISODIC,),
        ),
        context,
    )
    matching_source = [entry for entry in historical_source if entry.memory_id == source.memory_id]
    if len(matching_source) != 1:
        raise AssertionError("supersession must retain the source entry as historical memory state")
    _assert_canonical_type(matching_source[0], MemoryType.EPISODIC)
    if matching_source[0].superseded_by_memory_id != linked.memory_id:
        raise AssertionError("superseded memory must link to its canonical replacement")

    due = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.TASK,
        scope_id=scope_id,
        owner_ref=context.actor_ref,
        created_by=context.actor_ref,
        value={"text": "due preference"},
        created_at=datetime.now(UTC) - timedelta(hours=2),
        retention=RetentionPolicy.UNTIL,
        origin=MemoryOrigin.USER_AUTHORED,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        provenance=(SourceRef(kind="conformance", ref="expiry-source"),),
        metadata={"provider_private_type": "native::preference"},
        memory_type=MemoryType.PREFERENCE,
    )
    await provider.write_entry(due, context)
    due_query = MemoryQuery(
        MemoryScope.TASK,
        scope_id,
        include_expired=True,
        memory_types=(MemoryType.PREFERENCE,),
    )
    due_before = await provider.query_entries(due_query, context)
    matching_due = [entry for entry in due_before if entry.memory_id == due.memory_id]
    if len(matching_due) != 1:
        raise AssertionError("expired-inclusive query must expose the due entry before removal")
    _assert_canonical_type(matching_due[0], MemoryType.PREFERENCE)

    if _EXACT_EXPIRY_OPERATION in declared:
        expired = await provider.expire_entry(due.memory_id, due_query, context)
        _assert_canonical_type(expired, MemoryType.PREFERENCE)
        await _assert_not_found(provider, due.memory_id, context)
    else:
        try:
            await provider.expire_entry(due.memory_id, due_query, context)
        except ContractError as error:
            if error.code is not ErrorCode.UNSUPPORTED_CAPABILITY:
                raise AssertionError(
                    "unadvertised exact expiry must fail as unsupported_capability"
                ) from error
        else:
            raise AssertionError(
                "provider performed exact expiry without advertising the capability"
            )

    aggregate_due = replace(due, memory_id=new_memory_id(), memory_type=MemoryType.SEMANTIC)
    await provider.write_entry(aggregate_due, context)
    aggregate_before = await provider.query_entries(
        MemoryQuery(
            MemoryScope.TASK,
            scope_id,
            include_expired=True,
            memory_types=(MemoryType.SEMANTIC,),
        ),
        context,
    )
    aggregate_match = [
        entry for entry in aggregate_before if entry.memory_id == aggregate_due.memory_id
    ]
    if len(aggregate_match) != 1:
        raise AssertionError("aggregate expiry target must remain queryable before removal")
    _assert_canonical_type(aggregate_match[0], MemoryType.SEMANTIC)
    expired_ids = await provider.expire_entries(context)
    if aggregate_due.memory_id not in expired_ids:
        raise AssertionError("expire_entries must report removed canonical memory IDs")
    await _assert_not_found(provider, aggregate_due.memory_id, context)

    removable = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.TASK,
        scope_id=scope_id,
        owner_ref=context.actor_ref,
        created_by=context.actor_ref,
        value={"text": "delete procedural"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.TASK_LIFETIME,
        provenance=(SourceRef(kind="conformance", ref="delete-source"),),
        metadata={"provider_private_type": "native::procedure"},
        memory_type=MemoryType.PROCEDURAL,
    )
    await provider.write_entry(removable, context)
    before_delete = await provider.get_entry(removable.memory_id, context)
    _assert_canonical_type(before_delete, MemoryType.PROCEDURAL)
    await provider.delete_entry(removable.memory_id, context)
    await _assert_not_found(provider, removable.memory_id, context)

    if _DISCOVERY_OPERATION in declared:
        discovered = await provider.list_entries_for_discovery()
        discovered_by_id = {entry.memory_id: entry for entry in discovered}
        if linked.memory_id not in discovered_by_id:
            raise AssertionError("advertised discovery must return active canonical memory entries")
        for entry in discovered:
            if not isinstance(entry.memory_type, MemoryType):
                raise AssertionError("memory discovery leaked a provider-private type identifier")
        _assert_canonical_type(discovered_by_id[linked.memory_id], MemoryType.REFLECTIVE)
    else:
        try:
            await provider.list_entries_for_discovery()
        except ContractError as error:
            if error.code is not ErrorCode.UNSUPPORTED_CAPABILITY:
                raise AssertionError(
                    "unadvertised discovery must fail as unsupported_capability"
                ) from error
        else:
            raise AssertionError(
                "provider returned discovery data without advertising the capability"
            )


class FakeScopedMemoryProvider(MemoryProvider):
    """Deterministic non-SQLite provider for refined Memory contract testing."""

    def __init__(
        self,
        *,
        supports_exact_expiry: bool = True,
        supports_discovery: bool = True,
    ) -> None:
        operations = list(_REFINED_MEMORY_OPERATIONS)
        features = ["memory_type_taxonomy", "memory_type_filtering"]
        if supports_exact_expiry:
            operations.append(_EXACT_EXPIRY_OPERATION)
            features.append("exact_scoped_expiry")
        if supports_discovery:
            operations.append(_DISCOVERY_OPERATION)
            features.append("discovery_snapshot")
        operations.extend(("put", "get"))
        normalized_operations = tuple(dict.fromkeys(operations))
        capability = Capability(
            name="fake-scoped-memory",
            kind=CapabilityKind.MEMORY,
            supported_operations=normalized_operations,
            features=tuple(features),
        )
        self._descriptor = ProviderDescriptor(
            provider_id="fake-scoped-memory",
            provider_type="memory",
            supported_operations=normalized_operations,
            capabilities=(capability,),
            health=HealthStatus.HEALTHY,
        )
        self._supports_exact_expiry = supports_exact_expiry
        self._supports_discovery = supports_discovery
        self._coarse_values: dict[tuple[str, str], JsonValue] = {}
        self._entries: dict[str, MemoryEntry] = {}
        self._deleted: set[str] = set()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        del context
        self._coarse_values[(namespace, key)] = value
        return StoredObject(object_ref=f"memory:{namespace}:{key}", metadata=metadata or {})

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        del context
        try:
            return self._coarse_values[(namespace, key)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"memory key not found: {namespace}/{key}",
            ) from exc

    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        del context
        if entry.memory_id in self._entries:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"memory entry already exists: {entry.memory_id}",
            )
        self._entries[entry.memory_id] = entry
        return entry

    async def get_entry(self, memory_id: str, context: DataAccessContext) -> MemoryEntry:
        del context
        entry = self._entries.get(memory_id)
        if entry is None or memory_id in self._deleted or entry.expired:
            raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
        return entry

    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        del context
        now = datetime.now(UTC)
        entries: list[MemoryEntry] = []
        for entry in self._entries.values():
            if entry.memory_id in self._deleted:
                continue
            if entry.scope is not query.scope or entry.scope_id != query.scope_id:
                continue
            if query.owner_ref is not None and entry.owner_ref != query.owner_ref:
                continue
            if query.memory_types and entry.memory_type not in query.memory_types:
                continue
            if (
                not query.include_expired
                and entry.expires_at is not None
                and entry.expires_at <= now
            ):
                continue
            if not query.include_superseded and entry.superseded_by_memory_id is not None:
                continue
            entries.append(entry)
            if len(entries) >= query.limit:
                break
        return tuple(entries)

    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        needle = text.strip().casefold()
        if not needle:
            raise ContractError(ErrorCode.INVALID_REQUEST, "memory search text must not be blank")
        entries = await self.query_entries(query, context)
        return tuple(
            entry
            for entry in entries
            if needle in json.dumps(entry.value, sort_keys=True).casefold()
            or needle in json.dumps(entry.metadata, sort_keys=True).casefold()
        )

    async def supersede_entry(
        self,
        memory_id: str,
        replacement: MemoryEntry,
        context: DataAccessContext,
    ) -> MemoryEntry:
        current = await self.get_entry(memory_id, context)
        if replacement.scope is not current.scope or replacement.scope_id != current.scope_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement memory must remain in the same scope",
            )
        if replacement.owner_ref != current.owner_ref:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement memory must preserve owner_ref",
            )
        if replacement.supersedes_memory_id not in (None, memory_id):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement supersedes a different memory entry",
            )
        if replacement.memory_id in self._entries:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"memory entry already exists: {replacement.memory_id}",
            )
        linked = replace(replacement, supersedes_memory_id=memory_id)
        self._entries[memory_id] = replace(current, superseded_by_memory_id=linked.memory_id)
        self._entries[linked.memory_id] = linked
        return linked

    async def delete_entry(self, memory_id: str, context: DataAccessContext) -> None:
        await self.get_entry(memory_id, context)
        self._deleted.add(memory_id)

    async def expire_entry(
        self,
        memory_id: str,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> MemoryEntry:
        del context
        if not self._supports_exact_expiry:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "memory provider does not support exact scoped expiration",
            )
        entry = self._entries.get(memory_id)
        if entry is None or memory_id in self._deleted:
            raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
        if entry.scope is not query.scope or entry.scope_id != query.scope_id:
            raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
        if query.owner_ref is not None and entry.owner_ref != query.owner_ref:
            raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
        if query.memory_types and entry.memory_type not in query.memory_types:
            raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
        if entry.expires_at is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "memory entry has no expiration time")
        if entry.expires_at > datetime.now(UTC):
            raise ContractError(
                ErrorCode.CONFLICT,
                "memory entry has not reached its expiration time",
            )
        self._deleted.add(memory_id)
        return entry

    async def expire_entries(self, context: DataAccessContext) -> tuple[str, ...]:
        del context
        now = datetime.now(UTC)
        expired = tuple(
            entry.memory_id
            for entry in self._entries.values()
            if entry.memory_id not in self._deleted
            and entry.expires_at is not None
            and entry.expires_at <= now
        )
        self._deleted.update(expired)
        return expired

    async def list_entries_for_discovery(self) -> tuple[MemoryEntry, ...]:
        if not self._supports_discovery:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "memory provider does not support canonical discovery enumeration",
            )
        now = datetime.now(UTC)
        return tuple(
            entry
            for entry in self._entries.values()
            if entry.memory_id not in self._deleted
            and (entry.expires_at is None or entry.expires_at > now)
            and entry.superseded_by_memory_id is None
        )
