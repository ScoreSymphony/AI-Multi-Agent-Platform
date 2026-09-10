from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import (
    MemoryEntry,
    MemoryOrigin,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.portability import MemoryPortableCodec
from ai_multi_agent_platform.portability.models import PortableResource
from ai_multi_agent_platform.portability.registry import ImportContext


def test_memory_entry_preserves_pre_718_positional_constructor_order() -> None:
    created_at = datetime.now(UTC)
    entry = MemoryEntry(
        new_memory_id(),
        MemoryScope.USER,
        "user-a",
        "user:user-a",
        "user:user-a",
        {"legacy": True},
        created_at,
        RetentionPolicy.USER_LIFETIME,
        MemoryOrigin.IMPORTED,
        None,
        (),
        None,
        None,
        None,
        {"source": "legacy-positional"},
    )

    assert entry.origin is MemoryOrigin.IMPORTED
    assert entry.expires_at is None
    assert entry.metadata == {"source": "legacy-positional"}
    assert entry.memory_type is MemoryType.UNCLASSIFIED


def test_memory_query_preserves_pre_718_positional_constructor_order() -> None:
    query = MemoryQuery(
        MemoryScope.USER,
        "user-a",
        None,
        True,
        True,
        17,
    )

    assert query.include_expired is True
    assert query.include_superseded is True
    assert query.limit == 17
    assert query.memory_types == ()


def test_portable_memory_rejects_non_string_schema_version_canonically() -> None:
    resource = PortableResource(
        resource_type="memory",
        resource_id=new_memory_id(),
        resource_version="2",
        payload={
            "schema_version": ["2"],
            "entry": {},
            "source_project_id": None,
        },
    )

    with pytest.raises(ContractError) as exc_info:
        MemoryPortableCodec().deserialize(resource, ImportContext())

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
