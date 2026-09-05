from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates.models import (
    CapabilityRequirement,
    TemplateCompatibility,
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import (
    TemplateEnvironment,
    TemplateHandlerRegistry,
    TemplateService,
)


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="issue-78-compat-owner")


@dataclass
class _AgentHandler:
    created: list[str]
    template_type = TemplateType.AGENT

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (TemplateResourceChange(resource_type="agent", action="create"),)

    def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance
        resource_id = f"agent-{len(self.created) + 1}"
        self.created.append(resource_id)
        return (TemplateResourceRef(resource_type="agent", resource_id=resource_id),)


def _service() -> tuple[TemplateService, str, _AgentHandler]:
    repository = InMemoryTemplateRepository()
    handler = _AgentHandler(created=[])
    handlers = TemplateHandlerRegistry()
    handlers.register(handler)
    service = TemplateService(repository, handlers)
    draft = service.create_draft(
        owner_ref=_owner(),
        content=TemplateContent(
            name="Compatibility test",
            description="Version compatibility regression",
            template_type=TemplateType.AGENT,
            configuration=TemplateConfiguration(payload={}),
            requirements=TemplateRequirements(
                capabilities=(
                    CapabilityRequirement(
                        capability_id="tool.search",
                        version_constraint=">=2,<3",
                    ),
                ),
            ),
            compatibility=TemplateCompatibility(
                platform_version_range=">=2,<3",
                contract_versions={"agent": ">=2,<3"},
            ),
            provenance=TemplateProvenance(author="test", source="test"),
        ),
    )
    published = service.publish(draft.template_id, expected_revision=draft.revision)
    return service, published.template_id, handler


def test_version_incompatibilities_appear_in_preview_and_block_apply() -> None:
    service, template_id, handler = _service()
    environment = TemplateEnvironment(
        platform_version="1.9",
        contract_versions={"agent": "1.0"},
        capability_ids=frozenset({"tool.search"}),
        capability_versions={"tool.search": ("1.5", "3.0")},
    )

    preview = service.preview(
        template_id,
        applied_by=_owner(),
        environment=environment,
    )

    assert not preview.applicable
    assert preview.missing_required_capability_ids == ()
    assert len(preview.incompatible_platform_versions) == 1
    assert len(preview.incompatible_contract_versions) == 1
    assert len(preview.incompatible_capability_versions) == 1

    with pytest.raises(ContractError) as exc_info:
        service.apply(template_id, applied_by=_owner(), environment=environment)

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
    assert handler.created == []


def test_missing_version_inventories_fail_closed_for_versioned_requirements() -> None:
    service, template_id, handler = _service()
    environment = TemplateEnvironment(capability_ids=frozenset({"tool.search"}))

    preview = service.preview(
        template_id,
        applied_by=_owner(),
        environment=environment,
    )

    assert not preview.applicable
    assert len(preview.incompatible_platform_versions) == 1
    assert len(preview.missing_contract_versions) == 1
    assert len(preview.incompatible_capability_versions) == 1
    assert handler.created == []


def test_compatible_actual_versions_allow_apply() -> None:
    service, template_id, handler = _service()
    environment = TemplateEnvironment(
        platform_version="2.4.1",
        contract_versions={"agent": "2.1"},
        capability_ids=frozenset({"tool.search"}),
        capability_versions={"tool.search": ("1.0", "2.5")},
    )

    preview = service.preview(
        template_id,
        applied_by=_owner(),
        environment=environment,
    )
    assert preview.applicable
    assert preview.incompatible_platform_versions == ()
    assert preview.missing_contract_versions == ()
    assert preview.incompatible_contract_versions == ()
    assert preview.incompatible_capability_versions == ()

    result = service.apply(template_id, applied_by=_owner(), environment=environment)
    assert len(result.resource_refs) == 1
    assert handler.created == [result.resource_refs[0].resource_id]


def test_optional_capability_version_mismatch_is_visible_but_non_blocking() -> None:
    repository = InMemoryTemplateRepository()
    handler = _AgentHandler(created=[])
    handlers = TemplateHandlerRegistry()
    handlers.register(handler)
    service = TemplateService(repository, handlers)
    draft = service.create_draft(
        owner_ref=_owner(),
        content=TemplateContent(
            name="Optional compatibility",
            description="Optional version mismatch",
            template_type=TemplateType.AGENT,
            configuration=TemplateConfiguration(payload={}),
            requirements=TemplateRequirements(
                capabilities=(
                    CapabilityRequirement(
                        capability_id="tool.optional",
                        optional=True,
                        version_constraint=">=2",
                    ),
                ),
            ),
            provenance=TemplateProvenance(author="test", source="test"),
        ),
    )
    published = service.publish(draft.template_id, expected_revision=draft.revision)
    environment = TemplateEnvironment(
        capability_ids=frozenset({"tool.optional"}),
        capability_versions={"tool.optional": ("1.0",)},
    )

    preview = service.preview(
        published.template_id,
        applied_by=_owner(),
        environment=environment,
    )

    assert preview.applicable
    assert len(preview.incompatible_optional_capability_versions) == 1
