from __future__ import annotations

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
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateService
from ai_multi_agent_platform.templates.trust import activate_untrusted_revision

OWNER = OwnerRef(type="user", id="issue-78-trust-guard-owner")
ACTIVATOR = OwnerRef(type="user", id="issue-78-trust-guard-activator")


def _content(name: str, trust: TemplateTrust) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=name,
        template_type=TemplateType.COMPOSITE,
        configuration=TemplateConfiguration(payload={}),
        provenance=TemplateProvenance(
            author="test-author",
            source="test-source",
            trust=trust,
        ),
    )


def test_untrusted_publish_stays_untrusted_but_normal_revise_cannot_promote() -> None:
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=OWNER,
        content=_content("Imported draft", TemplateTrust.UNTRUSTED),
    )

    published = service.publish(draft.template_id, expected_revision=draft.revision)
    assert published.content.provenance.trust is TemplateTrust.UNTRUSTED

    with pytest.raises(ContractError) as exc_info:
        service.revise_draft(
            published.template_id,
            _content("Edited imported template", TemplateTrust.LOCAL),
            expected_revision=published.revision,
        )

    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert repository.get_template(published.template_id).current_revision == published.revision
    assert repository.list_revisions(published.template_id) == (draft, published)


def test_explicit_activation_is_the_only_allowed_untrusted_trust_promotion() -> None:
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=OWNER,
        content=_content("Imported draft", TemplateTrust.UNTRUSTED),
    )
    published = service.publish(draft.template_id, expected_revision=draft.revision)

    activated = activate_untrusted_revision(
        repository,
        published.template_id,
        expected_revision=published.revision,
        activated_by=ACTIVATOR,
    )

    assert activated.content.provenance.trust is TemplateTrust.TRUSTED
    assert activated.content.provenance.source_template == published.ref
    assert repository.get_template(published.template_id).current_revision == activated.revision

    revised = service.revise_draft(
        activated.template_id,
        _content("Locally edited after activation", TemplateTrust.LOCAL),
        expected_revision=activated.revision,
    )
    assert revised.content.provenance.trust is TemplateTrust.LOCAL
    assert repository.get_template(activated.template_id).current_revision == revised.revision
