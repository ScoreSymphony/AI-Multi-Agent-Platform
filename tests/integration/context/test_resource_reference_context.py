from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from conversation_completion_cases import (
    _data_context,
    _message,
    _response_request,
)

from ai_multi_agent_platform.conversations import (
    ReferenceKind,
    ResourceReference,
    resolve_conversation_context,
)
from ai_multi_agent_platform.data import (
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    new_knowledge_source_id,
)
from ai_multi_agent_platform.domain import new_id


def test_file_reference_is_resolved_into_ephemeral_model_context(tmp_path: Path) -> None:
    project_id = new_id("project")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    record = asyncio.run(
        files.create_file(
            b"deployment target is blue",
            _data_context(project_id),
            content_type="text/plain",
        )
    )
    reference = ResourceReference(kind=ReferenceKind.FILE, id=record.file_id)
    message = _message(references=(reference,))
    request = _response_request(message, project_id=project_id)

    resolved = asyncio.run(
        resolve_conversation_context(
            request,
            file_provider=files,
            knowledge_provider=None,
        )
    )

    assert len(resolved) == 1
    assert resolved[0].kind == "file"
    assert resolved[0].id == record.file_id
    assert "deployment target is blue" in resolved[0].text
    assert "deployment target is blue" not in str(message.to_json())
    assert record.file_id in str(message.to_json())


def test_binary_file_reference_is_metadata_only_even_when_bytes_are_utf8(tmp_path: Path) -> None:
    project_id = new_id("project")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    sensitive_binary_text = b"binary-marked-secret-that-happens-to-be-valid-utf8"
    record = asyncio.run(
        files.create_file(
            sensitive_binary_text,
            _data_context(project_id),
            content_type="application/octet-stream",
        )
    )
    message = _message(references=(ResourceReference(kind=ReferenceKind.FILE, id=record.file_id),))

    resolved = asyncio.run(
        resolve_conversation_context(
            _response_request(message, project_id=project_id),
            file_provider=files,
            knowledge_provider=None,
        )
    )

    assert len(resolved) == 1
    assert "binary content is not injected as text" in resolved[0].text
    assert "binary-marked-secret-that-happens-to-be-valid-utf8" not in resolved[0].text
    assert "application/octet-stream" in resolved[0].text


def test_knowledge_reference_is_resolved_into_ephemeral_model_context(tmp_path: Path) -> None:
    project_id = new_id("project")
    knowledge = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:alice",
        created_by="user:alice",
        title="Deployment notes",
        revision="v1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    context = _data_context(project_id)
    asyncio.run(knowledge.register_source(source, context))
    asyncio.run(
        knowledge.ingest_source(
            source.source_id,
            "deployment target blue remains canonical",
            "notes/deployment.md",
            context,
        )
    )
    message = _message(
        text="Which deployment target should I use?",
        references=(ResourceReference(kind=ReferenceKind.KNOWLEDGE, id=source.source_id),),
    )
    request = _response_request(message, project_id=project_id)

    resolved = asyncio.run(
        resolve_conversation_context(
            request,
            file_provider=None,
            knowledge_provider=knowledge,
        )
    )

    assert len(resolved) == 1
    assert resolved[0].kind == "knowledge"
    assert resolved[0].id == source.source_id
    assert "deployment target blue remains canonical" in resolved[0].text
    assert "deployment target blue remains canonical" not in str(message.to_json())
