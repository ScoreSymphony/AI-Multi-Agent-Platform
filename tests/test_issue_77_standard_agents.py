from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_TEMPLATES,
    STANDARD_TEAM_TEMPLATES,
    AgentCapabilityPolicy,
    AgentModelPolicy,
    AgentService,
    CapabilityConstraint,
    InMemoryAgentRepository,
    JsonAgentRepository,
    ModelFallbackPolicy,
    assess_standard_agent_capabilities,
    bootstrap_standard_agents,
    clone_standard_agent,
    clone_standard_team,
    ensure_standard_agent_capabilities,
    get_standard_agent_template,
)
from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import RoutingRequirements


class _CapabilityInventory:
    def __init__(self, *capability_ids: str) -> None:
        self._items = tuple(
            CapabilitySpec(capability_id=capability_id, name=capability_id)
            for capability_id in capability_ids
        )

    def inventory_capabilities(
        self,
        *,
        include_unavailable: bool = True,
    ) -> tuple[CapabilitySpec, ...]:
        del include_unavailable
        return self._items


def _service() -> AgentService:
    return AgentService(InMemoryAgentRepository())


def test_bootstrap_installs_all_standard_agents_and_teams_idempotently() -> None:
    service = _service()

    first = bootstrap_standard_agents(service)

    assert len(first.installed_agent_keys) == 8
    assert len(first.installed_team_keys) == 2
    assert first.preserved_agent_keys == ()
    assert first.preserved_team_keys == ()
    assert len(service.repository.list_agents()) == len(STANDARD_AGENT_TEMPLATES)
    assert len(service.repository.list_teams()) == len(STANDARD_TEAM_TEMPLATES)

    initial_agent_revisions = {
        item.agent_id: item.current_revision for item in service.repository.list_agents()
    }
    initial_team_revisions = {
        item.team_id: item.current_revision for item in service.repository.list_teams()
    }

    second = bootstrap_standard_agents(service)

    assert set(second.preserved_agent_keys) == {item.key for item in STANDARD_AGENT_TEMPLATES}
    assert set(second.preserved_team_keys) == {item.key for item in STANDARD_TEAM_TEMPLATES}
    assert second.installed_agent_keys == ()
    assert second.installed_team_keys == ()
    assert {
        item.agent_id: item.current_revision for item in service.repository.list_agents()
    } == initial_agent_revisions
    assert {
        item.team_id: item.current_revision for item in service.repository.list_teams()
    } == initial_team_revisions


def test_bootstrap_preserves_local_edits_and_team_base_revision_pins() -> None:
    service = _service()
    bootstrap_standard_agents(service)
    developer = get_standard_agent_template("developer")

    edited_profile = replace(
        service.get_agent_revision(developer.agent_id).profile,
        description="Locally customized standard developer",
    )
    service.update_agent(developer.agent_id, edited_profile, expected_revision=1)

    result = bootstrap_standard_agents(service)

    assert "developer" in result.preserved_agent_keys
    current = service.get_agent_revision(developer.agent_id)
    assert current.revision == 2
    assert current.profile.description == "Locally customized standard developer"

    software_team = next(
        item for item in STANDARD_TEAM_TEMPLATES if item.key == "software_development"
    )
    team_revision = service.get_team_revision(software_team.team_id)
    developer_member = next(
        member
        for member in team_revision.profile.members
        if member.agent.agent_id == developer.agent_id
    )
    assert developer_member.agent.revision == 1


def test_user_can_clone_and_edit_model_and_capability_policy() -> None:
    service = _service()
    bootstrap_standard_agents(service)
    owner = OwnerRef(type="user", id="user-77")

    cloned = clone_standard_agent(
        service,
        "developer",
        owner_ref=owner,
        name="My Developer",
    )

    replacement_model = AgentModelPolicy(
        requirements=RoutingRequirements(
            explicit_model_id="deployment-selected-model",
            modalities=("text",),
            tool_calling=True,
        ),
        allow_task_override=True,
        fallback=ModelFallbackPolicy.ROUTE,
    )
    replacement_capabilities = AgentCapabilityPolicy(
        allowed=("tool.file.read",),
        constraints=(CapabilityConstraint(capability_id="tool.file.read", required=True),),
    )
    edited = replace(
        cloned.profile,
        model=replacement_model,
        capabilities=replacement_capabilities,
        enabled=False,
    )
    updated = service.update_agent(
        cloned.agent_id,
        edited,
        expected_revision=cloned.revision,
    )

    assert cloned.agent_id != get_standard_agent_template("developer").agent_id
    assert cloned.owner_ref == owner
    assert updated.profile.name == "My Developer"
    assert updated.profile.model.requirements.explicit_model_id == "deployment-selected-model"
    assert updated.profile.capabilities.required_ids == ("tool.file.read",)
    assert updated.profile.enabled is False


def test_user_owned_agent_copy_can_be_deleted_and_delete_persists(tmp_path: Path) -> None:
    repository_path = tmp_path / "agents.json"
    owner = OwnerRef(type="user", id="user-77")
    other_owner = OwnerRef(type="user", id="different-user")
    service = AgentService(JsonAgentRepository(repository_path))
    bootstrap_standard_agents(service)
    cloned = clone_standard_agent(service, "developer", owner_ref=owner)

    with pytest.raises(ContractError) as wrong_owner:
        service.delete_agent(cloned.agent_id, expected_owner_ref=other_owner)
    assert wrong_owner.value.code is ErrorCode.FORBIDDEN

    service.delete_agent(cloned.agent_id, expected_owner_ref=owner)

    with pytest.raises(ContractError) as removed:
        service.get_agent_revision(cloned.agent_id)
    assert removed.value.code is ErrorCode.NOT_FOUND

    reloaded = AgentService(JsonAgentRepository(repository_path))
    with pytest.raises(ContractError) as still_removed:
        reloaded.get_agent_revision(cloned.agent_id)
    assert still_removed.value.code is ErrorCode.NOT_FOUND
    assert (
        reloaded.get_agent_revision(get_standard_agent_template("developer").agent_id).revision == 1
    )


def test_user_owned_team_copy_can_be_deleted_without_removing_bundled_team() -> None:
    service = _service()
    bootstrap_standard_agents(service)
    owner = OwnerRef(type="user", id="user-77")
    cloned = clone_standard_team(service, "research", owner_ref=owner, name="My Research Team")

    service.delete_team(cloned.team_id, expected_owner_ref=owner)

    with pytest.raises(ContractError) as removed:
        service.get_team_revision(cloned.team_id)
    assert removed.value.code is ErrorCode.NOT_FOUND
    bundled_research_team = next(item for item in STANDARD_TEAM_TEMPLATES if item.key == "research")
    assert service.get_team_revision(bundled_research_team.team_id).revision == 1


def test_bundled_agent_cannot_be_deleted_as_a_user_owned_copy() -> None:
    service = _service()
    bootstrap_standard_agents(service)
    owner = OwnerRef(type="user", id="user-77")
    developer = get_standard_agent_template("developer")

    with pytest.raises(ContractError) as exc_info:
        service.delete_agent(developer.agent_id, expected_owner_ref=owner)

    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert service.get_agent_revision(developer.agent_id).revision == 1


def test_missing_optional_capabilities_are_graceful() -> None:
    template = get_standard_agent_template("general_assistant")
    inventory = _CapabilityInventory()

    readiness = assess_standard_agent_capabilities(template, inventory)
    validated = ensure_standard_agent_capabilities(template, inventory)

    assert readiness.usable is True
    assert readiness.missing_required_capability_ids == ()
    assert readiness.missing_optional_capability_ids == (
        "tool.web.read",
        "tool.file.read",
    )
    assert validated == readiness


def test_missing_required_capability_is_explicit() -> None:
    template = get_standard_agent_template("developer")
    inventory = _CapabilityInventory("tool.file.write")

    readiness = assess_standard_agent_capabilities(template, inventory)
    assert readiness.usable is False
    assert readiness.missing_required_capability_ids == ("tool.file.read",)

    with pytest.raises(ContractError) as exc_info:
        ensure_standard_agent_capabilities(template, inventory)

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert exc_info.value.details["agent_key"] == "developer"
    assert exc_info.value.details["missing_required_capability_ids"] == ["tool.file.read"]


def test_security_sensitive_starters_keep_restrictive_defaults() -> None:
    researcher = get_standard_agent_template("researcher")
    developer = get_standard_agent_template("developer")
    administrator = get_standard_agent_template("system_administrator")

    assert "tool.file.write" in researcher.profile.capabilities.denied
    assert "tool.shell.execute" in researcher.profile.capabilities.denied
    assert "tool.shell.execute" not in developer.profile.capabilities.required_ids

    developer_shell = next(
        item
        for item in developer.profile.capabilities.constraints
        if item.capability_id == "tool.shell.execute"
    )
    assert developer_shell.required is False
    assert developer_shell.approval_ref == "approval:standard-shell-execution"

    assert administrator.profile.enabled is False
    administrator_shell = next(
        item
        for item in administrator.profile.capabilities.constraints
        if item.capability_id == "tool.shell.execute"
    )
    assert administrator_shell.required is True
    assert administrator_shell.approval_ref == "approval:standard-privileged-admin"


def test_bootstrap_can_report_readiness_without_blocking_optional_gaps() -> None:
    service = _service()
    inventory = _CapabilityInventory("tool.file.read", "tool.data.read", "tool.shell.execute")

    result = bootstrap_standard_agents(service, capability_inventory=inventory)
    readiness = {item.agent_key: item for item in result.readiness}

    assert readiness["general_assistant"].usable is True
    assert "tool.web.read" in readiness["general_assistant"].missing_optional_capability_ids
    assert readiness["developer"].usable is True
    assert readiness["data_analyst"].usable is True
    assert readiness["file_assistant"].usable is True
    assert readiness["system_administrator"].usable is True
