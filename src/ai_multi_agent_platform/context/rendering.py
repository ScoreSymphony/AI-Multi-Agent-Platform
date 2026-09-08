"""Replaceable rendering boundary for immutable Context Bundles."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from .models import (
    ContextBundle,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextSourceType,
)


class ContextRenderingError(RuntimeError):
    pass


class ContextContentProvider(Protocol):
    """Resolve one already-authorized canonical content reference at the consuming boundary."""

    async def resolve_text(self, entry: ContextEntry) -> str: ...


@dataclass(frozen=True, slots=True)
class RenderedContextPart:
    ordinal: int
    role: ContextEntryRole
    source_type: ContextSourceType
    source_id: str
    content: str
    content_digest: str
    mandatory: bool


@dataclass(frozen=True, slots=True)
class RenderedContext:
    """Provider-neutral rendering result retaining the canonical bundle identity."""

    context_bundle_id: str
    context_bundle_digest: str
    renderer_id: str
    parts: tuple[RenderedContextPart, ...]

    def __post_init__(self) -> None:
        if not self.renderer_id.strip():
            raise ValueError("context renderer ID must not be blank")
        if tuple(part.ordinal for part in self.parts) != tuple(range(len(self.parts))):
            raise ValueError("rendered context must preserve canonical entry ordering")


class ContextRenderer(Protocol):
    @property
    def renderer_id(self) -> str: ...

    async def render(
        self,
        bundle: ContextBundle,
        *,
        content_provider: ContextContentProvider | None = None,
        allow_secret_resolution: bool = False,
    ) -> RenderedContext: ...


class ReferenceContextRenderer:
    """Strict local renderer that may translate content but may not redefine the bundle."""

    renderer_id = "reference-context-renderer/v1"

    async def render(
        self,
        bundle: ContextBundle,
        *,
        content_provider: ContextContentProvider | None = None,
        allow_secret_resolution: bool = False,
    ) -> RenderedContext:
        parts: list[RenderedContextPart] = []
        for entry in bundle.entries:
            content = entry.inline_content
            if content is None:
                if (
                    entry.data_classification is ContextDataClassification.SECRET_REFERENCE
                    and not allow_secret_resolution
                ):
                    raise ContextRenderingError(
                        "secret reference requires an explicitly authorized consuming boundary"
                    )
                if content_provider is None:
                    raise ContextRenderingError(
                        f"context content provider required for {entry.content_ref!r}"
                    )
                content = await content_provider.resolve_text(entry)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest != entry.content_digest:
                raise ContextRenderingError(
                    f"context content digest changed for {entry.source.source_id!r}"
                )
            parts.append(
                RenderedContextPart(
                    ordinal=entry.ordinal,
                    role=entry.role,
                    source_type=entry.source.source_type,
                    source_id=entry.source.source_id,
                    content=content,
                    content_digest=entry.content_digest,
                    mandatory=entry.mandatory,
                )
            )
        return RenderedContext(
            context_bundle_id=bundle.context_bundle_id,
            context_bundle_digest=bundle.digest,
            renderer_id=self.renderer_id,
            parts=tuple(parts),
        )


def assert_render_preserves_bundle(bundle: ContextBundle, rendered: RenderedContext) -> None:
    """Reject adapter output that silently drops/reorders/replaces canonical context."""

    if rendered.context_bundle_id != bundle.context_bundle_id:
        raise ContextRenderingError("rendered context references a different Context Bundle")
    if rendered.context_bundle_digest != bundle.digest:
        raise ContextRenderingError("rendered context digest does not match Context Bundle")
    expected = tuple(
        (entry.ordinal, entry.content_digest, entry.mandatory) for entry in bundle.entries
    )
    actual = tuple((part.ordinal, part.content_digest, part.mandatory) for part in rendered.parts)
    if actual != expected:
        raise ContextRenderingError(
            "adapter rendering changed canonical ordering, content identity, or mandatory semantics"
        )
