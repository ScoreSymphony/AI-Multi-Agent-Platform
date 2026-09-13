from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_TEMPLATES,
    STANDARD_TEAM_TEMPLATES,
    STARTER_CATALOG_SOURCE,
    STARTER_CATALOG_VERSION,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
    clone_standard_agent,
)
from ai_multi_agent_platform.domain import OwnerRef


def test_standard_catalog_definitions_have_canonical_version_and_provenance_metadata() -> None:
    for template in STANDARD_AGENT_TEMPLATES:
        metadata = template.profile.metadata
        assert template.version == STARTER_CATALOG_VERSION
        assert metadata["starter_catalog_source"] == STARTER_CATALOG_SOURCE
        assert metadata["starter_catalog_version"] == STARTER_CATALOG_VERSION
        assert metadata["platform_release"] == __version__
        assert metadata["starter_key"] == template.key
        assert metadata["starter_kind"] == "agent"
        assert metadata["permission_profile"] == template.permission_profile
        assert metadata["changelog"] == template.changelog
        assert metadata["migration_notes"]
        assert template.profile.model.requirements.modalities == ("text",)
        assert template.profile.model.requirements.explicit_model_id is None
        assert not (
            set(template.profile.capabilities.allowed) & set(template.profile.capabilities.denied)
        )

    for template in STANDARD_TEAM_TEMPLATES:
        assert template.version == STARTER_CATALOG_VERSION
        assert template.members
        assert template.leader_agent_key in {member.agent_key for member in template.members}


def test_rebootstrap_does_not_mutate_user_owned_clone() -> None:
    service = AgentService(InMemoryAgentRepository())
    bootstrap_standard_agents(service)
    owner = OwnerRef(type="user", id="issue-77-upgrade-user")
    cloned = clone_standard_agent(service, "general_assistant", owner_ref=owner)
    customized = replace(
        cloned.profile,
        description="User-owned customization that a platform upgrade must preserve.",
        enabled=False,
    )
    customized_revision = service.update_agent(
        cloned.agent_id,
        customized,
        expected_revision=cloned.revision,
    )

    bootstrap_standard_agents(service)

    after_upgrade = service.get_agent_revision(cloned.agent_id)
    assert after_upgrade == customized_revision
    assert after_upgrade.owner_ref == owner
    assert after_upgrade.profile.description == customized.description
    assert after_upgrade.profile.enabled is False
