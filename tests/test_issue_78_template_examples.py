from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.templates import TemplateType, validate_template_configuration
from ai_multi_agent_platform.templates.codec import template_content_from_json

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "templates"


@pytest.mark.parametrize(
    ("filename", "expected_type"),
    [
        ("project.template.json", TemplateType.PROJECT),
        ("composite-starter.template.json", TemplateType.COMPOSITE),
    ],
)
def test_template_examples_are_canonical_safe_content(
    filename: str,
    expected_type: TemplateType,
) -> None:
    raw = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))

    content = template_content_from_json(raw)
    validate_template_configuration(content.configuration)

    assert content.template_type is expected_type
    assert content.compatibility.provider_agnostic is True
    assert content.compatibility.orchestrator_agnostic is True
    assert content.provenance.metadata["example"] is True
