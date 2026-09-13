from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelRoutingProfileRef, new_model_routing_profile_id
from ai_multi_agent_platform.portability.routing_profile_reference_audit import (
    build_routing_profile_dependency_audit,
)
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateRequirements,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateService

OWNER = OwnerRef(type="user", id="user-issue-447-audit")


def _agent_profile(reference: str) -> AgentProfile:
    return AgentProfile(
        name="Issue 447 Agent",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Exercise routing-profile reference auditing.")
        ),
        model=AgentModelPolicy(routing_profile_ref=reference),
    )


def test_reference_audit_proves_absence_across_empty_canonical_stores() -> None:
    audit = build_routing_profile_dependency_audit(
        agents=InMemoryAgentRepository(),
        templates=InMemoryTemplateRepository(),
    )

    assert audit(new_model_routing_profile_id()) == ()


def test_reference_audit_reports_all_agent_revisions_that_reference_profile() -> None:
    profile_id = new_model_routing_profile_id()
    first_ref = ModelRoutingProfileRef(profile_id, 1).canonical_ref
    second_ref = ModelRoutingProfileRef(profile_id, 2).canonical_ref
    agents = AgentService(InMemoryAgentRepository())
    first = agents.create_agent(_agent_profile(first_ref), owner_ref=OWNER)
    agents.update_agent(first.agent_id, _agent_profile(second_ref), expected_revision=1)
    audit = build_routing_profile_dependency_audit(agents=agents.repository)

    assert audit(profile_id) == (
        f"agent:{first.agent_id}@r1",
        f"agent:{first.agent_id}@r2",
    )


def test_reference_audit_reports_template_requirement_and_embedded_agent_reference() -> None:
    profile_id = new_model_routing_profile_id()
    reference = ModelRoutingProfileRef(profile_id, 3).canonical_ref
    templates = TemplateService(InMemoryTemplateRepository())
    revision = templates.create_draft(
        owner_ref=OWNER,
        content=TemplateContent(
            name="Issue 447 Template",
            description="Canonical routing-profile consumer",
            template_type=TemplateType.AGENT,
            configuration=TemplateConfiguration(
                payload={"profile": {"model": {"routing_profile_ref": reference}}}
            ),
            requirements=TemplateRequirements(model_policy_refs=(reference,)),
        ),
    )
    audit = build_routing_profile_dependency_audit(
        agents=InMemoryAgentRepository(),
        templates=templates.repository,
    )

    assert audit(profile_id) == (f"template:{revision.template_id}@r1",)


def test_reference_audit_fails_closed_for_malformed_target_shaped_reference() -> None:
    profile_id = new_model_routing_profile_id()
    agents = AgentService(InMemoryAgentRepository())
    agents.create_agent(_agent_profile(f"{profile_id}@rX"), owner_ref=OWNER)
    audit = build_routing_profile_dependency_audit(agents=agents.repository)

    assert audit(profile_id) is None


def test_reference_audit_combines_additional_consumer_domains() -> None:
    profile_id = new_model_routing_profile_id()
    audit = build_routing_profile_dependency_audit(
        agents=InMemoryAgentRepository(),
        additional_audit=lambda audited_id: (f"custom:{audited_id}",),
    )

    assert audit(profile_id) == (f"custom:{profile_id}",)
