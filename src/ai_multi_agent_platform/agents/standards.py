"""Provider-neutral standard Agent and Agent Team starters for issue #77."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.capabilities.types import CapabilitySpec
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.models import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models.types import RoutingRequirements

from .models import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevision,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevision,
    CapabilityConstraint,
    InstructionSource,
    ModelFallbackPolicy,
)
from .service import AgentService

STARTER_CATALOG_SOURCE = "ai-multi-agent-platform.standard-agents"
STARTER_CATALOG_VERSION = "1.0.0"
STARTER_PLATFORM_RELEASE = __version__
STARTER_OWNER = OwnerRef(type="service", id="platform:standard-agent-catalog")

STANDARD_AGENT_IDS: Mapping[str, str] = MappingProxyType(
    {
        "general_assistant": "agent_c96def53-54a7-5a11-982c-6f2a615b2fdb",
        "planner": "agent_73d222c5-cea1-5b21-b22c-aebd9300ec6e",
        "researcher": "agent_772296b9-f4ea-57b0-9da8-fb2b95761e8a",
        "developer": "agent_7d75429f-204d-5af5-8d8c-b68947b154fc",
        "reviewer": "agent_f2523e5a-04b8-51c4-9719-0b119ac92eab",
        "data_analyst": "agent_5a886733-7b2d-5cdb-b6a2-5eb0018b9495",
        "file_assistant": "agent_7aca8da9-7686-57d8-a267-5c8f907cc984",
        "system_administrator": "agent_bc8fea68-0147-5260-af83-66ac6b127b54",
    }
)
STANDARD_TEAM_IDS: Mapping[str, str] = MappingProxyType(
    {
        "software_development": "team_300c9c45-1c4a-53bc-9942-c588b6c9dd71",
        "research": "team_a3f77665-9cab-586c-a64f-99aa66e59ca3",
    }
)


class CapabilityInventory(Protocol):
    """Minimal #12 inventory seam used for starter readiness checks."""

    def inventory_capabilities(
        self,
        *,
        include_unavailable: bool = True,
    ) -> tuple[CapabilitySpec, ...]: ...


@dataclass(frozen=True, slots=True)
class StandardAgentTemplate:
    key: str
    agent_id: str
    version: str
    profile: AgentProfile
    permission_profile: str
    changelog: str

    @property
    def required_capability_ids(self) -> tuple[str, ...]:
        return self.profile.capabilities.required_ids

    @property
    def optional_capability_ids(self) -> tuple[str, ...]:
        return tuple(
            constraint.capability_id
            for constraint in self.profile.capabilities.constraints
            if not constraint.required
        )


@dataclass(frozen=True, slots=True)
class StandardTeamMemberTemplate:
    agent_key: str
    role: str
    required: bool = True
    can_delegate_to_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StandardTeamTemplate:
    key: str
    team_id: str
    version: str
    name: str
    description: str
    members: tuple[StandardTeamMemberTemplate, ...]
    leader_agent_key: str
    max_parallel_agents: int
    max_steps: int
    changelog: str


@dataclass(frozen=True, slots=True)
class StandardAgentReadiness:
    agent_key: str
    missing_required_capability_ids: tuple[str, ...]
    missing_optional_capability_ids: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return not self.missing_required_capability_ids


@dataclass(frozen=True, slots=True)
class StarterBootstrapResult:
    installed_agent_keys: tuple[str, ...]
    preserved_agent_keys: tuple[str, ...]
    installed_team_keys: tuple[str, ...]
    preserved_team_keys: tuple[str, ...]
    readiness: tuple[StandardAgentReadiness, ...] = ()


@dataclass(frozen=True, slots=True)
class _AgentSpec:
    key: str
    name: str
    role: str
    description: str
    instruction: str
    permission_profile: str
    changelog: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    approvals: tuple[tuple[str, str], ...] = ()
    memory_scopes: tuple[MemoryScope, ...] = (MemoryScope.TASK,)
    enabled: bool = True


def _metadata(
    *,
    key: str,
    kind: str,
    permission_profile: str,
    changelog: str,
) -> dict[str, JsonValue]:
    return {
        "starter_catalog": True,
        "starter_catalog_source": STARTER_CATALOG_SOURCE,
        "starter_catalog_version": STARTER_CATALOG_VERSION,
        "platform_release": STARTER_PLATFORM_RELEASE,
        "starter_key": key,
        "starter_kind": kind,
        "permission_profile": permission_profile,
        "changelog": changelog,
        "migration_notes": "Initial definition; no automatic migration is required.",
    }


def _provenance(*, key: str, kind: str, action: str) -> Provenance:
    return Provenance(
        source=STARTER_CATALOG_SOURCE,
        actor_ref="platform:standard-agent-catalog",
        details={
            "action": action,
            "starter_key": key,
            "starter_kind": kind,
            "starter_catalog_version": STARTER_CATALOG_VERSION,
            "platform_release": STARTER_PLATFORM_RELEASE,
        },
    )


def _profile(spec: _AgentSpec) -> AgentProfile:
    approval_refs = dict(spec.approvals)
    allowed = (*spec.required, *spec.optional)
    capabilities = AgentCapabilityPolicy(
        allowed=allowed,
        denied=spec.denied,
        constraints=tuple(
            CapabilityConstraint(
                capability_id=capability_id,
                required=capability_id in spec.required,
                approval_ref=approval_refs.get(capability_id),
            )
            for capability_id in allowed
        ),
    )
    return AgentProfile(
        name=spec.name,
        role=spec.role,
        description=spec.description,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=spec.instruction,
                version=STARTER_CATALOG_VERSION,
            )
        ),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(modalities=("text",)),
            allow_task_override=True,
            fallback=ModelFallbackPolicy.ROUTE,
        ),
        capabilities=capabilities,
        data_access=AgentDataAccess(memory_scopes=spec.memory_scopes),
        enabled=spec.enabled,
        metadata=_metadata(
            key=spec.key,
            kind="agent",
            permission_profile=spec.permission_profile,
            changelog=spec.changelog,
        ),
    )


_AGENT_SPECS = (
    _AgentSpec(
        key="general_assistant",
        name="General Assistant",
        role="general_assistant",
        description="General-purpose assistant with conservative optional read capabilities.",
        instruction=(
            "Handle general tasks through provider-neutral platform contracts. Use only granted "
            "capabilities and prefer least-privileged actions. Never assume a specific model, "
            "provider, orchestrator, executor, memory backend, host, or credential source."
        ),
        permission_profile="least-privilege-general",
        changelog="Initial general-purpose assistant starter.",
        optional=("tool.web.read", "tool.file.read"),
        denied=("tool.shell.execute",),
        memory_scopes=(MemoryScope.TASK, MemoryScope.AGENT),
    ),
    _AgentSpec(
        key="planner",
        name="Planner",
        role="planner",
        description="Planning specialist that decomposes goals without performing execution.",
        instruction=(
            "Translate goals into ordered plans with dependencies, assumptions, risks, acceptance "
            "criteria, and handoff points. Plan against canonical platform capabilities instead "
            "of provider-specific tools. Do not perform destructive or privileged execution."
        ),
        permission_profile="planning-only",
        changelog="Initial planning specialist starter.",
        denied=("tool.file.write", "tool.shell.execute"),
        memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
    ),
    _AgentSpec(
        key="researcher",
        name="Researcher",
        role="researcher",
        description="Research specialist with optional read-only web and file capabilities.",
        instruction=(
            "Research claims from available sources, preserve source references, and distinguish "
            "evidence from inference. Prefer authoritative primary sources where available. "
            "Operate read-only by default and never use destructive filesystem or shell actions."
        ),
        permission_profile="research-read-only",
        changelog="Initial read-oriented researcher starter.",
        optional=("tool.web.read", "tool.file.read"),
        denied=("tool.file.write", "tool.shell.execute"),
        memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
    ),
    _AgentSpec(
        key="developer",
        name="Developer",
        role="developer",
        description="Software-development specialist scoped to an assigned workspace.",
        instruction=(
            "Analyze, implement, and test changes inside the assigned project/workspace. Use "
            "canonical capabilities and keep changes reviewable. Never assume credentials, "
            "unrestricted host access, or permission to act outside the assigned scope."
        ),
        permission_profile="workspace-developer",
        changelog="Initial scoped software-development starter.",
        required=("tool.file.read",),
        optional=("tool.file.write", "tool.shell.execute"),
        approvals=(("tool.shell.execute", "approval:standard-shell-execution"),),
        memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
    ),
    _AgentSpec(
        key="reviewer",
        name="Reviewer",
        role="reviewer",
        description="Independent reviewer/tester with read-only defaults.",
        instruction=(
            "Review plans, code, artifacts, evidence, and results independently. Identify defects, "
            "regressions, unsupported claims, missing tests, and policy violations. Prefer "
            "verification over modification and do not silently repair reviewed work."
        ),
        permission_profile="independent-review-read-only",
        changelog="Initial independent reviewer/tester starter.",
        optional=("tool.file.read",),
        denied=("tool.file.write", "tool.shell.execute"),
    ),
    _AgentSpec(
        key="data_analyst",
        name="Data Analyst",
        role="data_analyst",
        description="Structured-data analyst with read-oriented defaults.",
        instruction=(
            "Analyze structured data, state assumptions, retain reproducible steps, and separate "
            "observed values from interpretation. Do not mutate source datasets unless an "
            "explicitly granted write capability and task require it."
        ),
        permission_profile="data-analysis-read-only",
        changelog="Initial data-analysis starter.",
        optional=("tool.data.read", "tool.file.read"),
        denied=("tool.file.write", "tool.shell.execute"),
        memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
    ),
    _AgentSpec(
        key="file_assistant",
        name="File Assistant",
        role="file_assistant",
        description="File specialist restricted to platform-assigned project/workspace scope.",
        instruction=(
            "Inspect and organize files only through platform capabilities and only within the "
            "assigned project/workspace scope. Treat writes as optional elevated functionality; "
            "never infer broader filesystem access from a path supplied by a user."
        ),
        permission_profile="workspace-files-scoped",
        changelog="Initial scoped file-assistant starter.",
        required=("tool.file.read",),
        optional=("tool.file.write",),
        denied=("tool.shell.execute",),
        memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
    ),
    _AgentSpec(
        key="system_administrator",
        name="System Administrator",
        role="system_administrator",
        description="Privileged operations starter; disabled by default and approval-gated.",
        instruction=(
            "Perform system administration only inside explicitly authorized infrastructure "
            "scope. Privileged or mutating actions require the canonical approval path. Never "
            "bypass policy, expand credentials, or treat this profile as implicit host authority."
        ),
        permission_profile="restricted-admin-approval-required",
        changelog="Initial disabled-by-default system-administration starter.",
        required=("tool.shell.execute",),
        optional=("tool.file.read", "tool.file.write"),
        approvals=(
            ("tool.shell.execute", "approval:standard-privileged-admin"),
            ("tool.file.write", "approval:standard-privileged-admin"),
        ),
        enabled=False,
    ),
)

STANDARD_AGENT_TEMPLATES: tuple[StandardAgentTemplate, ...] = tuple(
    StandardAgentTemplate(
        key=spec.key,
        agent_id=STANDARD_AGENT_IDS[spec.key],
        version=STARTER_CATALOG_VERSION,
        profile=_profile(spec),
        permission_profile=spec.permission_profile,
        changelog=spec.changelog,
    )
    for spec in _AGENT_SPECS
)

STANDARD_TEAM_TEMPLATES: tuple[StandardTeamTemplate, ...] = (
    StandardTeamTemplate(
        key="software_development",
        team_id=STANDARD_TEAM_IDS["software_development"],
        version=STARTER_CATALOG_VERSION,
        name="Software Development Team",
        description="Planner + Developer + independent Reviewer/Tester starter team.",
        members=(
            StandardTeamMemberTemplate(
                "planner",
                "planner",
                can_delegate_to_keys=("developer", "reviewer"),
            ),
            StandardTeamMemberTemplate(
                "developer",
                "developer",
                can_delegate_to_keys=("reviewer",),
            ),
            StandardTeamMemberTemplate("reviewer", "reviewer_tester"),
        ),
        leader_agent_key="planner",
        max_parallel_agents=2,
        max_steps=16,
        changelog="Initial software-development starter team.",
    ),
    StandardTeamTemplate(
        key="research",
        team_id=STANDARD_TEAM_IDS["research"],
        version=STARTER_CATALOG_VERSION,
        name="Research Team",
        description="Researcher + source-checking Reviewer + Data Analyst starter team.",
        members=(
            StandardTeamMemberTemplate(
                "researcher",
                "researcher",
                can_delegate_to_keys=("reviewer", "data_analyst"),
            ),
            StandardTeamMemberTemplate("reviewer", "source_checker_reviewer"),
            StandardTeamMemberTemplate("data_analyst", "analyst_writer"),
        ),
        leader_agent_key="researcher",
        max_parallel_agents=3,
        max_steps=16,
        changelog="Initial research starter team.",
    ),
)

_AGENT_TEMPLATES_BY_KEY = MappingProxyType(
    {template.key: template for template in STANDARD_AGENT_TEMPLATES}
)
_TEAM_TEMPLATES_BY_KEY = MappingProxyType(
    {template.key: template for template in STANDARD_TEAM_TEMPLATES}
)


def get_standard_agent_template(key: str) -> StandardAgentTemplate:
    try:
        return _AGENT_TEMPLATES_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"unknown standard Agent key: {key}") from exc


def get_standard_team_template(key: str) -> StandardTeamTemplate:
    try:
        return _TEAM_TEMPLATES_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"unknown standard Agent Team key: {key}") from exc


def assess_standard_agent_capabilities(
    template: StandardAgentTemplate,
    inventory: CapabilityInventory,
) -> StandardAgentReadiness:
    available = {
        capability.capability_id
        for capability in inventory.inventory_capabilities(include_unavailable=False)
    }
    missing_required = tuple(
        capability_id
        for capability_id in template.required_capability_ids
        if capability_id not in available
    )
    missing_optional = tuple(
        capability_id
        for capability_id in template.optional_capability_ids
        if capability_id not in available
    )
    return StandardAgentReadiness(
        agent_key=template.key,
        missing_required_capability_ids=missing_required,
        missing_optional_capability_ids=missing_optional,
    )


def ensure_standard_agent_capabilities(
    template: StandardAgentTemplate,
    inventory: CapabilityInventory,
) -> StandardAgentReadiness:
    readiness = assess_standard_agent_capabilities(template, inventory)
    if readiness.missing_required_capability_ids:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"standard Agent {template.key!r} is missing required capabilities",
            details={
                "agent_key": template.key,
                "missing_required_capability_ids": list(readiness.missing_required_capability_ids),
                "missing_optional_capability_ids": list(readiness.missing_optional_capability_ids),
            },
        )
    return readiness


def _validate_existing_agent_identity(
    service: AgentService,
    template: StandardAgentTemplate,
) -> AgentRevision:
    initial = service.repository.get_agent_revision(template.agent_id, 1)
    metadata = initial.profile.metadata
    if (
        metadata.get("starter_catalog_source") != STARTER_CATALOG_SOURCE
        or metadata.get("starter_key") != template.key
        or metadata.get("starter_kind") != "agent"
    ):
        prefix = "stable standard Agent ID is occupied by a non-catalog definition: "
        raise ContractError(
            ErrorCode.CONFLICT,
            prefix + template.agent_id,
            details={"agent_key": template.key, "agent_id": template.agent_id},
        )
    return service.get_agent_revision(template.agent_id)


def _materialize_team_profile(
    template: StandardTeamTemplate,
    base_agents: Mapping[str, AgentRevision],
) -> AgentTeamProfile:
    member_ids = {key: revision.agent_id for key, revision in base_agents.items()}
    members = tuple(
        AgentTeamMember(
            agent=AgentRevisionRef(
                agent_id=base_agents[member.agent_key].agent_id,
                revision=base_agents[member.agent_key].revision,
            ),
            role=member.role,
            required=member.required,
            can_delegate_to=tuple(
                member_ids[target_key] for target_key in member.can_delegate_to_keys
            ),
        )
        for member in template.members
    )
    return AgentTeamProfile(
        name=template.name,
        description=template.description,
        members=members,
        leader_agent_id=member_ids[template.leader_agent_key],
        max_parallel_agents=template.max_parallel_agents,
        max_steps=template.max_steps,
        metadata=_metadata(
            key=template.key,
            kind="team",
            permission_profile="member-policies-remain-authoritative",
            changelog=template.changelog,
        ),
    )


def _validate_existing_team_identity(
    service: AgentService,
    template: StandardTeamTemplate,
) -> AgentTeamRevision:
    initial = service.repository.get_team_revision(template.team_id, 1)
    metadata = initial.profile.metadata
    if (
        metadata.get("starter_catalog_source") != STARTER_CATALOG_SOURCE
        or metadata.get("starter_key") != template.key
        or metadata.get("starter_kind") != "team"
    ):
        prefix = "stable standard Agent Team ID is occupied by a non-catalog definition: "
        raise ContractError(
            ErrorCode.CONFLICT,
            prefix + template.team_id,
            details={"team_key": template.key, "team_id": template.team_id},
        )
    return service.get_team_revision(template.team_id)


def bootstrap_standard_agents(
    service: AgentService,
    *,
    capability_inventory: CapabilityInventory | None = None,
    owner_ref: OwnerRef = STARTER_OWNER,
) -> StarterBootstrapResult:
    """Install missing starters without mutating an existing starter identity."""

    existing_agent_ids = {agent.agent_id for agent in service.repository.list_agents()}
    installed_agents: list[str] = []
    preserved_agents: list[str] = []
    base_agents: dict[str, AgentRevision] = {}

    for template in STANDARD_AGENT_TEMPLATES:
        if template.agent_id in existing_agent_ids:
            _validate_existing_agent_identity(service, template)
            preserved_agents.append(template.key)
        else:
            service.create_agent(
                template.profile,
                owner_ref=owner_ref,
                provenance=_provenance(
                    key=template.key,
                    kind="agent",
                    action="bootstrap",
                ),
                agent_id=template.agent_id,
            )
            installed_agents.append(template.key)
        base_agents[template.key] = service.repository.get_agent_revision(template.agent_id, 1)

    existing_team_ids = {team.team_id for team in service.repository.list_teams()}
    installed_teams: list[str] = []
    preserved_teams: list[str] = []
    for template in STANDARD_TEAM_TEMPLATES:
        if template.team_id in existing_team_ids:
            _validate_existing_team_identity(service, template)
            preserved_teams.append(template.key)
        else:
            service.create_team(
                _materialize_team_profile(template, base_agents),
                owner_ref=owner_ref,
                provenance=_provenance(
                    key=template.key,
                    kind="team",
                    action="bootstrap",
                ),
                team_id=template.team_id,
            )
            installed_teams.append(template.key)

    readiness: tuple[StandardAgentReadiness, ...] = ()
    if capability_inventory is not None:
        readiness = tuple(
            assess_standard_agent_capabilities(template, capability_inventory)
            for template in STANDARD_AGENT_TEMPLATES
        )

    return StarterBootstrapResult(
        installed_agent_keys=tuple(installed_agents),
        preserved_agent_keys=tuple(preserved_agents),
        installed_team_keys=tuple(installed_teams),
        preserved_team_keys=tuple(preserved_teams),
        readiness=readiness,
    )


def clone_standard_agent(
    service: AgentService,
    key: str,
    *,
    owner_ref: OwnerRef,
    name: str | None = None,
) -> AgentRevision:
    """Clone bundled revision 1 into a user-owned canonical Agent identity."""

    template = get_standard_agent_template(key)
    _validate_existing_agent_identity(service, template)
    return service.clone_agent(
        template.agent_id,
        revision=1,
        owner_ref=owner_ref,
        name=name,
        provenance=_provenance(key=key, kind="agent", action="clone"),
    )


def clone_standard_team(
    service: AgentService,
    key: str,
    *,
    owner_ref: OwnerRef,
    name: str | None = None,
) -> AgentTeamRevision:
    """Clone bundled revision 1 into a user-owned canonical Agent Team identity."""

    template = get_standard_team_template(key)
    _validate_existing_team_identity(service, template)
    return service.clone_team(
        template.team_id,
        revision=1,
        owner_ref=owner_ref,
        name=name,
        provenance=_provenance(key=key, kind="team", action="clone"),
    )
