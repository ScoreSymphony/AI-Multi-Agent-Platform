"""Ephemeral canonical context resolution for conversational responses (#72).

Conversation history stores only canonical references. This module resolves those
references at response time through the replaceable File/Knowledge provider contracts,
under the current actor/project context, and passes bounded context to the selected
response provider without copying source content into chat persistence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    FileProvider,
    FileState,
    KnowledgeProvider,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
)

from .models import ContentKind, ReferenceKind, ResourceReference
from .responses import (
    ConversationResolvedContext,
    ConversationResponseChunk,
    ConversationResponseProvider,
    ConversationResponseRequest,
)

MAX_FILE_CONTEXT_BYTES = 128 * 1024
MAX_KNOWLEDGE_CONTEXT_RESULTS = 5
MAX_KNOWLEDGE_CONTEXT_CHARS = 64 * 1024


class ContextResolvingConversationResponseProvider:
    """Decorate any response provider with authorized canonical attachment context."""

    def __init__(
        self,
        inner: ConversationResponseProvider,
        *,
        file_provider: FileProvider | None = None,
        knowledge_provider: KnowledgeProvider | None = None,
    ) -> None:
        self._inner = inner
        self._file_provider = file_provider
        self._knowledge_provider = knowledge_provider

    def stream_response(
        self,
        request: ConversationResponseRequest,
    ) -> AsyncIterator[ConversationResponseChunk]:
        async def stream() -> AsyncIterator[ConversationResponseChunk]:
            resolved = await resolve_conversation_context(
                request,
                file_provider=self._file_provider,
                knowledge_provider=self._knowledge_provider,
            )
            enriched = replace(request, resolved_context=resolved)
            async for chunk in self._inner.stream_response(enriched):
                yield chunk

        return stream()


async def resolve_conversation_context(
    request: ConversationResponseRequest,
    *,
    file_provider: FileProvider | None,
    knowledge_provider: KnowledgeProvider | None,
) -> tuple[ConversationResolvedContext, ...]:
    """Resolve File/Knowledge references without persisting their source content."""

    references = _context_references(request)
    if not references:
        return ()
    access = _data_access_context(request)
    query = _source_query(request)
    resolved: list[ConversationResolvedContext] = []
    for reference in references:
        if reference.kind is ReferenceKind.FILE:
            if file_provider is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "conversation file context requires a configured canonical FileProvider",
                    details={"file_id": reference.id},
                )
            resolved.append(await _resolve_file(file_provider, reference.id, access))
        elif reference.kind is ReferenceKind.KNOWLEDGE:
            if knowledge_provider is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    (
                        "conversation knowledge context requires a configured canonical "
                        "KnowledgeProvider"
                    ),
                    details={"knowledge_source_id": reference.id},
                )
            resolved.append(
                await _resolve_knowledge(knowledge_provider, reference.id, query, access)
            )
    return tuple(resolved)


async def _resolve_file(
    provider: FileProvider,
    file_id: str,
    context: DataAccessContext,
) -> ConversationResolvedContext:
    record = await provider.get_file(file_id, context)
    if record.state is not FileState.READY:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "conversation file context is not ready",
            details={"file_id": file_id, "state": record.state.value},
        )

    content, truncated = await _bounded_file_bytes(provider, file_id, context)
    decoded = _decode_text_file(content, record.content_type)
    if decoded is None:
        text = (
            f"Canonical File {file_id}: binary content is not injected as text; "
            f"content_type={record.content_type or 'unknown'}, size_bytes={record.size_bytes}, "
            f"sha256={record.sha256}."
        )
    else:
        suffix = "\n[File context truncated at 131072 bytes.]" if truncated else ""
        text = (
            f"Canonical File {file_id} ({record.content_type or 'text/unknown'}, "
            f"sha256={record.sha256}):\n{decoded}{suffix}"
        )
    return ConversationResolvedContext(kind="file", id=file_id, text=text)


async def _bounded_file_bytes(
    provider: FileProvider,
    file_id: str,
    context: DataAccessContext,
) -> tuple[bytes, bool]:
    remaining = MAX_FILE_CONTEXT_BYTES
    chunks: list[bytes] = []
    truncated = False
    async for chunk in provider.stream_file(file_id, context):
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            remaining = 0
            truncated = True
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks), truncated


def _decode_text_file(data: bytes, content_type: str | None) -> str | None:
    textual = (
        content_type is None
        or content_type.startswith("text/")
        or content_type
        in {
            "application/json",
            "application/ld+json",
            "application/xml",
            "application/x-yaml",
            "application/yaml",
        }
    )
    if textual:
        return data.decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def _resolve_knowledge(
    provider: KnowledgeProvider,
    source_id: str,
    query: str,
    context: DataAccessContext,
) -> ConversationResolvedContext:
    # Re-check source visibility/index existence under the current response actor before search.
    await provider.get_index_status(source_id, context)
    results = await provider.search(
        KnowledgeSearchRequest(
            query=query,
            context=context,
            source_ids=(source_id,),
            mode=KnowledgeSearchMode.KEYWORD,
            limit=MAX_KNOWLEDGE_CONTEXT_RESULTS,
        )
    )
    if not results:
        text = (
            f"Canonical Knowledge source {source_id}: no matching context for the current message."
        )
    else:
        parts: list[str] = []
        remaining = MAX_KNOWLEDGE_CONTEXT_CHARS
        for result in results:
            entry = f"[{result.location}]\n{result.content}"
            if len(entry) > remaining:
                parts.append(entry[:remaining])
                remaining = 0
                break
            parts.append(entry)
            remaining -= len(entry)
            if remaining <= 0:
                break
        text = f"Canonical Knowledge source {source_id}:\n" + "\n\n".join(parts)
        if remaining <= 0:
            text += "\n[Knowledge context truncated.]"
    return ConversationResolvedContext(kind="knowledge", id=source_id, text=text)


def _context_references(request: ConversationResponseRequest) -> tuple[ResourceReference, ...]:
    ordered: list[ResourceReference] = []
    seen: set[tuple[ReferenceKind, str]] = set()
    for message in request.history:
        candidates = list(message.references)
        candidates.extend(
            block.reference for block in message.content if block.reference is not None
        )
        for reference in candidates:
            if reference.kind not in {ReferenceKind.FILE, ReferenceKind.KNOWLEDGE}:
                continue
            key = (reference.kind, reference.id)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(reference)
    return tuple(ordered)


def _source_query(request: ConversationResponseRequest) -> str:
    source = request.history[-1]
    parts = [
        block.text.strip()
        for block in source.content
        if block.kind in {ContentKind.TEXT, ContentKind.MARKDOWN}
        and block.text is not None
        and block.text.strip()
    ]
    return "\n".join(parts) or "conversation context"


def _data_access_context(request: ConversationResponseRequest) -> DataAccessContext:
    owner_type, owner_id = _owner_from_actor_ref(request.actor_ref)
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=request.correlation_id,
            causation_id=request.source_message_id,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=request.project_id,
        ),
        actor_ref=request.actor_ref,
    )


def _owner_from_actor_ref(actor_ref: str) -> tuple[str | None, str | None]:
    owner_type, separator, owner_id = actor_ref.partition(":")
    if not separator or not owner_type or not owner_id:
        return None, None
    return owner_type, owner_id


__all__ = [
    "ContextResolvingConversationResponseProvider",
    "MAX_FILE_CONTEXT_BYTES",
    "MAX_KNOWLEDGE_CONTEXT_RESULTS",
    "resolve_conversation_context",
]
