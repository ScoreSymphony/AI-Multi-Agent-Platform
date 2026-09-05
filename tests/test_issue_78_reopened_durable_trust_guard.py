from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateProvenance,
    TemplateTrust,
    TemplateType,
)
from ai_multi_agent_platform.templates.persistence import JsonTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateService

OWNER = OwnerRef(type="user", id="issue-78-durable-trust-owner")


def _content(name: str, trust: TemplateTrust) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=name,
        template_type=TemplateType.COMPOSITE,
        configuration=TemplateConfiguration(payload={}),
        provenance=TemplateProvenance(
            author="test",
            source="test",
            trust=trust,
        ),
    )


def test_json_repository_persists_untrusted_lineage_when_implicit_promotion_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "templates.json"
    repository = JsonTemplateRepository(path)
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=OWNER,
        content=_content("Imported", TemplateTrust.UNTRUSTED),
    )
    published = service.publish(draft.template_id, expected_revision=draft.revision)

    with pytest.raises(ContractError) as exc_info:
        service.revise_draft(
            published.template_id,
            _content("Implicit promotion", TemplateTrust.LOCAL),
            expected_revision=published.revision,
        )

    assert exc_info.value.code is ErrorCode.FORBIDDEN

    restored = JsonTemplateRepository(path)
    definition = restored.get_template(published.template_id)
    persisted = restored.get_revision(published.template_id, published.revision)
    assert definition.current_revision == published.revision
    assert persisted.content.provenance.trust is TemplateTrust.UNTRUSTED
    assert restored.list_revisions(published.template_id) == (draft, published)
