"""Public JSON codec seam for canonical Template content."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import TemplateContent
from .persistence import _content, _content_to_json


def template_content_from_json(value: object) -> TemplateContent:
    """Decode one canonical Template content document using the durable schema rules."""

    return _content(value)


def template_content_to_json(item: TemplateContent) -> dict[str, JsonValue]:
    """Encode one canonical Template content document without runtime/private state."""

    return _content_to_json(item)
