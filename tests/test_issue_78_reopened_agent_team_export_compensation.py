from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    AgentTeamTemplateExporter,
    InMemoryTemplateRepository,
    TemplateService,
)
from ai_multi_agent_platform.templates.models import TemplateDefinition, TemplateRevision

OWNER = OwnerRef(type="user", id="issue-78-export-owner")


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(role=InstructionSource(content=f"Act as {name}.")),
    )


def _team(agents: AgentService) -> str:
    first = agents.create_agent(_profile("Planner"), owner_ref=OWNER)
    second = agents.create_agent(_profile("Reviewer"), owner_ref=OWNER)
    team = agents.create_team(
        AgentTeamProfile(
            name="Compensated export team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(first.agent_id, first.revision),
                    role="planner",
                    can_delegate_to=(second.agent_id,),
                ),
                AgentTeamMember(
                    agent=AgentRevisionRef(second.agent_id, second.revision),
                    role="reviewer",
                ),
            ),
            leader_agent_id=first.agent_id,
        ),
        owner_ref=OWNER,
    )
    return team.team_id


@dataclass
class _FailingTemplateRepository(InMemoryTemplateRepository):
    fail_create_number: int | None = None
    fail_append_number: int | None = None
    fail_after_mutation: bool = True

    def __post_init__(self) -> None:
        InMemoryTemplateRepository.__init__(self)
        self.create_calls = 0
        self.append_calls = 0

    def create_template(
        self,
        definition: TemplateDefinition,
        revision: TemplateRevision,
    ) -> None:
        self.create_calls += 1
        should_fail = self.create_calls == self.fail_create_number
        if should_fail and not self.fail_after_mutation:
            raise RuntimeError("simulated create failure")
        super().create_template(definition, revision)
        if should_fail:
            raise RuntimeError("simulated create failure after persistence")

    def append_revision(
        self,
        definition: TemplateDefinition,
        revision: TemplateRevision,
    ) -> None:
        self.append_calls += 1
        should_fail = self.append_calls == self.fail_append_number
        if should_fail and not self.fail_after_mutation:
            raise RuntimeError("simulated publish failure")
        super().append_revision(definition, revision)
        if should_fail:
            raise RuntimeError("simulated publish failure after persistence")


def test_parent_creation_failure_removes_all_published_child_templates() -> None:
    agents = AgentService(InMemoryAgentRepository())
    team_id = _team(agents)
    repository = _FailingTemplateRepository(fail_create_number=3)
    exporter = AgentTeamTemplateExporter(agents, TemplateService(repository))

    with pytest.raises(RuntimeError, match="create failure"):
        exporter.create_from_team(
            team_id,
            owner_ref=OWNER,
            author="user:exporter",
        )

    assert repository.create_calls == 3
    assert repository.append_calls == 2
    assert repository.list_templates() == ()


def test_child_publish_failure_removes_published_and_draft_children() -> None:
    agents = AgentService(InMemoryAgentRepository())
    team_id = _team(agents)
    repository = _FailingTemplateRepository(fail_append_number=2)
    exporter = AgentTeamTemplateExporter(agents, TemplateService(repository))

    with pytest.raises(RuntimeError, match="publish failure"):
        exporter.create_from_team(
            team_id,
            owner_ref=OWNER,
            author="user:exporter",
        )

    assert repository.create_calls == 2
    assert repository.append_calls == 2
    assert repository.list_templates() == ()


def test_cleanup_failure_surfaces_original_export_and_compensation_evidence() -> None:
    class CleanupFailingRepository(_FailingTemplateRepository):
        def delete_template(self, template_id: str) -> None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "simulated cleanup refusal",
                details={"template_id": template_id},
            )

    agents = AgentService(InMemoryAgentRepository())
    team_id = _team(agents)
    repository = CleanupFailingRepository(fail_append_number=2)
    exporter = AgentTeamTemplateExporter(agents, TemplateService(repository))

    with pytest.raises(ContractError) as exc_info:
        exporter.create_from_team(
            team_id,
            owner_ref=OWNER,
            author="user:exporter",
        )

    assert exc_info.value.code is ErrorCode.BACKEND_ERROR
    assert exc_info.value.details["export_error_type"] == "RuntimeError"
    assert exc_info.value.details["cleanup_failures"]
