from __future__ import annotations

from datetime import UTC, datetime

from ai_multi_agent_platform.data import (
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.portability import MemoryPortableCodec, MemoryPortableSnapshot
from ai_multi_agent_platform.portability.models import PortableResource
from ai_multi_agent_platform.portability.registry import ImportContext


def _entry() -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="agent:memory-curator",
        value={"preference": "compact"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        origin=MemoryOrigin.AGENT_DERIVED,
        memory_type=MemoryType.PREFERENCE,
    )


def test_portable_memory_v2_round_trips_origin_and_memory_type() -> None:
    codec = MemoryPortableCodec()
    entry = _entry()

    exported = codec.serialize(MemoryPortableSnapshot(entry=entry))
    assert exported.resource_version == "2"
    assert exported.payload["schema_version"] == "2"
    serialized_entry = exported.payload["entry"]
    assert isinstance(serialized_entry, dict)
    assert serialized_entry["origin"] == "agent-derived"
    assert serialized_entry["memory_type"] == "preference"

    restored = codec.deserialize(
        PortableResource(
            resource_type="memory",
            resource_id=entry.memory_id,
            resource_version="2",
            payload=exported.payload,
        ),
        ImportContext(),
    )
    assert isinstance(restored, MemoryPortableSnapshot)
    assert restored.entry.origin is MemoryOrigin.AGENT_DERIVED
    assert restored.entry.memory_type is MemoryType.PREFERENCE


def test_portable_memory_v1_imports_as_explicit_unclassified_legacy_state() -> None:
    codec = MemoryPortableCodec()
    entry = _entry()
    exported = codec.serialize(MemoryPortableSnapshot(entry=entry))
    v1_payload = dict(exported.payload)
    v1_payload["schema_version"] = "1"
    serialized_entry = dict(v1_payload["entry"])
    serialized_entry.pop("origin")
    serialized_entry.pop("memory_type")
    v1_payload["entry"] = serialized_entry

    restored = codec.deserialize(
        PortableResource(
            resource_type="memory",
            resource_id=entry.memory_id,
            resource_version="1",
            payload=v1_payload,
        ),
        ImportContext(),
    )
    assert isinstance(restored, MemoryPortableSnapshot)
    assert restored.entry.origin is MemoryOrigin.USER_AUTHORED
    assert restored.entry.memory_type is MemoryType.UNCLASSIFIED
