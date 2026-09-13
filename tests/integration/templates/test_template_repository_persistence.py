from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates.models import (
    CapabilityRequirement,
    TemplateCompatibility,
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiation,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceRef,
    TemplateTrust,
    TemplateType,
)
from ai_multi_agent_platform.templates.persistence import (
    TEMPLATE_REPOSITORY_SCHEMA_VERSION,
    JsonTemplateRepository,
)
from ai_multi_agent_platform.templates.service import TemplateService


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="persistent-template-user")


def _content(name: str) -> TemplateContent:
    return TemplateContent(
        name=name,
        description="Durable Agent template",
        template_type=TemplateType.AGENT,
        configuration=TemplateConfiguration(
            payload={
                "profile": {
                    "name": name,
                    "enabled": True,
                    "labels": ("portable", "durable"),
                }
            }
        ),
        requirements=TemplateRequirements(
            capabilities=(
                CapabilityRequirement(
                    capability_id="tool.files.read",
                    version_constraint=">=1",
                ),
            ),
            plugin_ids=("plugin.example",),
            model_policy_refs=("routing.default",),
            placeholders=("workspace_name",),
        ),
        compatibility=TemplateCompatibility(
            platform_version_range=">=1",
            contract_versions={"agent": "2"},
            metadata={"portable": True},
        ),
        provenance=TemplateProvenance(
            author="test",
            source="local-test",
            trust=TemplateTrust.TRUSTED,
            metadata={"origin": "fixture"},
        ),
        tags=("agent", "portable"),
        categories=("testing",),
    )


def test_json_repository_restores_revisions_and_instantiations(tmp_path) -> None:
    path = tmp_path / "templates.json"
    repository = JsonTemplateRepository(path)
    service = TemplateService(repository)

    draft = service.create_draft(owner_ref=_owner(), content=_content("Research Agent"))
    published = service.publish(draft.template_id, expected_revision=draft.revision)
    revised = service.revise_draft(
        draft.template_id,
        replace(_content("Research Agent v2"), tags=("agent", "portable", "v2")),
        expected_revision=published.revision,
    )
    instance = TemplateInstantiation(
        source=published.ref,
        applied_by=_owner(),
        resource_refs=(TemplateResourceRef("agent", "agent-created-from-template"),),
    )
    repository.record_instantiation(instance)

    restored = JsonTemplateRepository(path)

    assert restored.get_template(draft.template_id) == repository.get_template(draft.template_id)
    assert restored.list_revisions(draft.template_id) == repository.list_revisions(
        draft.template_id
    )
    assert restored.get_revision(draft.template_id, revised.revision) == revised
    assert restored.get_instantiation(instance.instance_id) == instance
    assert restored.list_instantiations(draft.template_id) == (instance,)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == TEMPLATE_REPOSITORY_SCHEMA_VERSION
    assert document["revisions"][0]["content"]["configuration"]["payload"]["profile"]["labels"] == [
        "portable",
        "durable",
    ]


def test_json_repository_rejects_unknown_schema_version(tmp_path) -> None:
    path = tmp_path / "templates.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "999",
                "templates": [],
                "revisions": [],
                "instantiations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported Template repository schema version"):
        JsonTemplateRepository(path)


def test_json_repository_rejects_noncontiguous_revision_history(tmp_path) -> None:
    path = tmp_path / "templates.json"
    repository = JsonTemplateRepository(path)
    service = TemplateService(repository)
    draft = service.create_draft(owner_ref=_owner(), content=_content("Agent"))
    published = service.publish(draft.template_id, expected_revision=draft.revision)
    service.revise_draft(
        draft.template_id,
        _content("Agent v2"),
        expected_revision=published.revision,
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    document["revisions"] = [
        item for item in document["revisions"] if item["revision"] != published.revision
    ]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="revision history is not contiguous"):
        JsonTemplateRepository(path)
