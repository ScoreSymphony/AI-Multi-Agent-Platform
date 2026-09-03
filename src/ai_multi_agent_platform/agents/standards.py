"""Editable standard Agent and Agent Team starter catalog for issue #77.

The catalog deliberately materializes the existing canonical Agent contracts from issue
#33. It does not introduce a second Agent schema and it does not depend on a concrete
model provider, orchestrator, executor, capability provider, memory backend or host.

Bundled definitions use stable IDs so a deployment can bootstrap them idempotently.
Bootstrap never updates an existing bundled identity: local edits remain untouched and
new platform releases cannot silently overwrite user-modified revisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

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
    """Small #12-compatible seam used for optional starter readiness checks."""

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
            item.capability_id
            for item in self.profile.capabilities.constraints
            if not item.required
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
        "starter_key": key,
        "starter_kind": kind,
        "permission_profile": permission_profile,
        "changelog": changelog,
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
        },
    )


def _model_policy(*, tool_calling: bool) -> AgentModelPolicy:
    return AgentModelPolicy(
        requirements=RoutingRequirements(
            tool_calling=tool_calling,
            modalities=("text",),
        ),
        allow_task_override=True,
        fallback=ModelFallbackPolicy.ROUTE,
    )


def _capability_policy(
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
    approval_refs: Mapping[str, str] | None = None,
) -> AgentCapabilityPolicy:
    approval_refs = approval_refs or {}
    allowed = (*required, *optional)
    return AgentCapabilityPolicy(
        allowed=allowed,
        denied=denied,
        constraints=tuple(
            CapabilityConstraint(
                capability_id=capability_id,
                required=capability_id in required,
                approval_ref=approval_refs.get(capability_id),
            )
            for capability_id in allowed
        ),
    )


def _profile(
    *,
    key: str,
    name: str,
    role: str,
    description: str,
    instruction: str,
    permission_profile: str,
    required_capabilities: tuple[str, ...] = (),
    optional_capabilities: tuple[str, ...] = (),
    denied_capabilities: tuple[str, ...] = (),
    approval_refs: Mapping[str, str] | None = None,
    memory_scopes: tuple[MemoryScope, ...] = (MemoryScope.TASK,),
    enabled: bool = True,
    changelog: str = "Initial standard definition for issue #77.",
) -> AgentProfile:
    has_tools = bool(required_capabilities or optional_capabilities)
    return AgentProfile(
        name=name,
        role=role,
        description=description,
        instructions=AgentInstructions(
            role=InstructionSource(content=instruction, version=STARTER_CATALOG_VERSION)
        ),
        model=_model_policy(tool_calling=has_tools),
        capabilities=_capability_policy(
            required=required_capabilities,
            optional=optional_capabilities,
            denied=denied_capabilities,
            approval_refs=approval_refs,
        ),
        data_access=AgentDataAccess(memory_scopes=memory_scopes),
        enabled=enabled,
        metadata=_metadata(
            key=key,
            kind="agent",
            permission_profile=permission_profile,
            changelog=changelog,
        ),
    )


STANDARD_AGENT_TEMPLATES: tuple[StandardAgentTemplate, ...] = (
    StandardAgentTemplate(
        key="general_assistant",
        agent_id=STANDARD_AGENT_IDS["general_assistant"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="least-privilege-general",
        changelog="Initial general-purpose assistant starter.",
        profile=_profile(
            key="general_assistant",
            name="General Assistant",
            role="general_assistant",
            description="General-purpose assistant with conservative, optional read capabilities.",
            instruction=(
                "Handle general user tasks using provider-neutral platform contracts. "
                "Use only capabilities granted for the current task and prefer read-only, "
                "least-privileged actions. Do not assume a specific model, tool provider, "
                "orchestrator, executor, memory backend, host, or credential source."
            ),
            permission_profile="least-privilege-general",
            optional_capabilities=("tool.web.read", "tool.file.read"),
            denied_capabilities=("tool.shell.execute",),
            memory_scopes=(MemoryScope.TASK, MemoryScope.AGENT),
        ),
    ),
    StandardAgentTemplate(
        key="planner",
        agent_id=STANDARD_AGENT_IDS["planner"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="planning-only",
        changelog="Initial planning specialist starter.",
        profile=_profile(
            key="planner",
            name="Planner",
            role="planner",
            description="Planning specialist that decomposes goals without performing execution.",
            instruction=(
                "Translate goals into explicit, ordered plans with dependencies, assumptions, "
                "risks, acceptance criteria, and handoff points. Plan against canonical platform "
                "capabilities instead of provider-specific tools. Do not perform destructive or "
                "privileged execution."
            ),
            permission_profile="planning-only",
            denied_capabilities=("tool.file.write", "tool.shell.execute"),
            memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
        ),
    ),
    StandardAgentTemplate(
        key="researcher",
        agent_id=STANDARD_AGENT_IDS["researcher"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="research-read-only",
        changelog="Initial read-oriented researcher starter.",
        profile=_profile(
            key="researcher",
            name="Researcher",
            role="researcher",
            description="Research specialist with optional read-only web and file capabilities.",
            instruction=(
                "Research claims from available sources, preserve source references and distinguish "
                "evidence from inference. Prefer authoritative primary sources when available. "
                "Operate read-only by default and never use destructive filesystem or shell actions."
            ),
            permission_profile="research-read-only",
            optional_capabilities=("tool.web.read", "tool.file.read"),
            denied_capabilities=("tool.file.write", "tool.shell.execute"),
            memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
        ),
    ),
    StandardAgentTemplate(
        key="developer",
        agent_id=STANDARD_AGENT_IDS["developer"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="workspace-developer",
        changelog="Initial scoped software-development starter.",
        profile=_profile(
            key="developer",
            name="Developer",
            role="developer",
            description="Software-development specialist scoped to an assigned workspace.",
            instruction=(
                "Analyze, implement, and test software changes inside the assigned project/workspace. "
                "Use canonical capabilities and keep changes reviewable. Never assume credentials, "
                "secrets, unrestricted host access, or permission to act outside the assigned scope."
            ),
            permission_profile="workspace-developer",
            required_capabilities=("tool.file.read",),
            optional_capabilities=("tool.file.write", "tool.shell.execute"),
            approval_refs={"tool.shell.execute": "approval:standard-shell-execution"},
            memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
        ),
    ),
    StandardAgentTemplate(
        key="reviewer",
        agent_id=STANDARD_AGENT_IDS["reviewer"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="independent-review-read-only",
        changelog="Initial independent reviewer/tester starter.",
        profile=_profile(
            key="reviewer",
            name="Reviewer",
            role="reviewer",
            description="Independent reviewer/tester with read-only defaults.",
            instruction=(
                "Review plans, code, artifacts, evidence, and results independently. Identify concrete "
                "defects, regressions, unsupported claims, missing tests, and policy violations. "
                "Prefer verification over modification and do not silently repair the work being reviewed."
            ),
            permission_profile="independent-review-read-only",
            optional_capabilities=("tool.file.read",),
            denied_capabilities=("tool.file.write", "tool.shell.execute"),
            memory_scopes=(MemoryScope.TASK,),
        ),
    ),
    StandardAgentTemplate(
        key="data_analyst",
        agent_id=STANDARD_AGENT_IDS["data_analyst"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="data-analysis-read-only",
        changelog="Initial data-analysis starter.",
        profile=_profile(
            key="data_analyst",
            name="Data Analyst",
            role="data_analyst",
            description="Structured-data analyst with read-oriented defaults.",
            instruction=(
                "Inspect and analyze structured data, state assumptions, retain reproducible steps, "
                "and separate observed values from interpretation. Do not mutate source datasets "
                "unless an explicitly granted write capability and task require it."
            ),
            permission_profile="data-analysis-read-only",
            required_capabilities=("tool.data.read",),
            optional_capabilities=("tool.file.read",),
            denied_capabilities=("tool.file.write", "tool.shell.execute"),
            memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
        ),
    ),
    StandardAgentTemplate(
        key="file_assistant",
        agent_id=STANDARD_AGENT_IDS["file_assistant"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="workspace-files-scoped",
        changelog="Initial scoped file-assistant starter.",
        profile=_profile(
            key="file_assistant",
            name="File Assistant",
            role="file_assistant",
            description="File specialist restricted to platform-assigned project/workspace scope.",
            instruction=(
                "Inspect and organize files only through platform capabilities and only within the "
                "assigned project/workspace scope. Treat writes as optional elevated functionality; "
                "never infer broader filesystem access from a file path supplied by a user."
            ),
            permission_profile="workspace-files-scoped",
            required_capabilities=("tool.file.read",),
            optional_capabilities=("tool.file.write",),
            denied_capabilities=("tool.shell.execute",),
            memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
        ),
    ),
    StandardAgentTemplate(
        key="system_administrator",
        agent_id=STANDARD_AGENT_IDS["system_administrator"],
        version=STARTER_CATALOG_VERSION,
        permission_profile="restricted-admin-approval-required",
        changelog="Initial disabled-by-default system-administration starter.",
        profile=_profile(
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
            required_capabilities=("tool.shell.execute",),
            optional_capabilities=("tool.file.read", "tool.file.write"),
            approval_refs={
                "tool.shell.execute": "approval:standard-privileged-admin",
                "tool.file.write": "approval:standard-privileged-admin",
            },
            memory_scopes=(MemoryScope.TASK,),
            enabled=False,
        ),
    ),
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
                agent_key="planner",
                role="planner",
                can_delegate_to_keys=("developer", "reviewer"),
            ),
            StandardTeamMemberTemplate(
                agent_key="developer",
                role="developer",
                can_delegate_to_keys=("reviewer",),
            ),
            StandardTeamMemberTemplate(agent_key="reviewer", role="reviewer_tester"),
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
                agent_key="researcher",
                role="researcher",
                can_delegate_to_keys=("reviewer", "data_analyst"),
            ),
            StandardTeamMemberTemplate(agent_key="reviewer", role="source_checker_reviewer"),
            StandardTeamMemberTemplate(agent_key="data_analyst", role="analyst_writer"),
        ),
        leader_agent_key="researcher",
        max_parallel_agents=3,
        max_steps=16,
        changelog="Initial research starter team.",
    ),
)

_AGENT_TEMPLATES_BY_KEY: Mapping[str, StandardAgentTemplate] = MappingProxyType(
    {item.key: item for item in STANDARD_AGENT_TEMPLATES}
)
_TEAM_TEMPLATES_BY_KEY: Mapping[str, StandardTeamTemplate] = MappingProxyType(
    {item.key: item for item in STANDARD_TEAM_TEMPLATES}
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
    available_ids = {
        capability.capability_id
        for capability in inventory.inventory_capabilities(include_unavailable=False)
    }
    missing_required = tuple(
        capability_id
        for capability_id in template.required_capability_ids
        if capability_id not in available_ids
    )
    missing_optional = tuple(
        capability_id
        for capability_id in template.optional_capability_ids
        if capability_id not in available_ids
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
        raise ContractError(
            ErrorCode.CONFLICT,
            f"stable standard Agent ID is occupied by a non-catalog definition: {template.agent_id}",
            details={"agent_key": template.key, "agent_id": template.agent_id},
        )
    return service.get_agent_revision(template.agent_id)


def _materialize_team_profile(
    template: StandardTeamTemplate,
    base_agent_revisions: Mapping[str, AgentRevision],
) -> AgentTeamProfile:
    member_ids = {key: base_agent_revisions[key].agent_id for key in base_agent_revisions}
    members = tuple(
        AgentTeamMember(
            agent=AgentRevisionRef(
                agent_id=base_agent_revisions[member.agent_key].agent_id,
                revision=base_agent_revisions[member.agent_key].revision,
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
        raise ContractError(
            ErrorCode.CONFLICT,
            f"stable standard Agent Team ID is occupied by a non-catalog definition: {template.team_id}",
            details={"team_key": template.key, "team_id": template.team_id},
        )
    return service.get_team_revision(template.team_id)


def bootstrap_standard_agents(
    service: AgentService,
    *,
    capability_inventory: CapabilityInventory | None = None,
    owner_ref: OwnerRef = STARTER_OWNER,
) -> StarterBootstrapResult:
    """Install missing starter definitions without mutating existing identities.

    Existing bundled identities are preserved at their current revision. Starter teams are
    initially pinned to revision 1 of the bundled Agents so a local edit of a standard Agent
    cannot silently change the meaning of a subsequently installed starter team.
    """

    existing_agent_ids = {item.agent_id for item in service.repository.list_agents()}
    installed_agent_keys: list[str] = []
    preserved_agent_keys: list[str] = []
    base_agent_revisions: dict[str, AgentRevision] = {}

    for template in STANDARD_AGENT_TEMPLATES:
        if template.agent_id in existing_agent_ids:
            _validate_existing_agent_identity(service, template)
            preserved_agent_keys.append(template.key)
        else:
            service.create_agent(
                template.profile,
                owner_ref=owner_ref,
                provenance=_provenance(key=template.key, kind="agent", action="bootstrap"),
                agent_id=template.agent_id,
            )
            installed_agent_keys.append(template.key)
        base_agent_revisions[template.key] = service.repository.get_agent_revision(
            template.agent_id, 1
        )

    existing_team_ids = {item.team_id for item in service.repository.list_teams()}
    installed_team_keys: list[str] = []
    preserved_team_keys: list[str] = []
    for template in STANDARD_TEAM_TEMPLATES:
        if template.team_id in existing_team_ids:
            _validate_existing_team_identity(service, template)
            preserved_team_keys.append(template.key)
            continue
        service.create_team(
            _materialize_team_profile(template, base_agent_revisions),
            owner_ref=owner_ref,
            provenance=_provenance(key=template.key, kind="team", action="bootstrap"),
            team_id=template.team_id,
        )
        installed_team_keys.append(template.key)

    readiness: tuple[StandardAgentReadiness, ...] = ()
    if capability_inventory is not None:
        readiness = tuple(
            assess_standard_agent_capabilities(template, capability_inventory)
            for template in STANDARD_AGENT_TEMPLATES
        )

    return StarterBootstrapResult(
        installed_agent_keys=tuple(installed_agent_keys),
        preserved_agent_keys=tuple(preserved_agent_keys),
        installed_team_keys=tuple(installed_team_keys),
        preserved_team_keys=tuple(preserved_team_keys),
        readiness=readiness,
    )


def clone_standard_agent(
    service: AgentService,
    key: str,
    *,
    owner_ref: OwnerRef,
    name: str | None = None,
) -> AgentRevision:
    """Create a user-owned editable copy of the immutable bundled base revision."""

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
    """Create a user-owned editable copy of the bundled starter Team revision."""

    template = get_standard_team_template(key)
    _validate_existing_team_identity(service, template)
    return service.clone_team(
        template.team_id,
        revision=1,
        owner_ref=owner_ref,
        name=name,
        provenance=_provenance(key=key, kind="team", action="clone"),
    )
