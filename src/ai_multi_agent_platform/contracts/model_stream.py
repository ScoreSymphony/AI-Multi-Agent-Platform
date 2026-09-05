"""Provider-neutral model streaming contracts and events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .types import AdapterMetadata, JsonValue, ModelRequest, ModelResponse


class ModelStreamEventKind(StrEnum):
    """Canonical event kinds emitted by streaming model providers."""

    TEXT_DELTA = "text_delta"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ModelStreamEvent:
    """One provider-neutral incremental model event.

    Provider-native stream/session objects never cross this boundary. ``model_ref``
    is normalized by ``ModelRuntime`` to the canonical model configuration ID.
    ``COMPLETED`` carries the same ``ModelResponse`` shape returned by ``generate``.
    """

    kind: ModelStreamEventKind
    request_id: str
    model_ref: str
    text_delta: str = ""
    finish_reason: str | None = None
    usage: dict[str, JsonValue] = field(default_factory=dict)
    response: ModelResponse | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("model stream request_id must not be blank")
        if not self.model_ref.strip():
            raise ValueError("model stream model_ref must not be blank")
        if self.finish_reason is not None and not self.finish_reason.strip():
            raise ValueError("model stream finish_reason must not be blank")

        if self.kind is ModelStreamEventKind.TEXT_DELTA:
            if not self.text_delta:
                raise ValueError("text_delta events require non-empty text")
            if self.response is not None:
                raise ValueError("text_delta events must not carry a final response")
            return

        if self.text_delta:
            raise ValueError("completed model stream events must not carry text_delta")
        if self.response is None:
            raise ValueError("completed model stream events require a final response")
        if self.response.request_id != self.request_id:
            raise ValueError("stream event and final response request_id must match")


@runtime_checkable
class StreamingModelProvider(Protocol):
    """Optional streaming extension for the canonical ``ModelProvider`` seam.

    Providers that can stream implement this structural extension. ``ModelRuntime``
    keeps ordinary ``ModelProvider`` implementations compatible through a deterministic
    one-chunk fallback built from ``generate``.
    """

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
