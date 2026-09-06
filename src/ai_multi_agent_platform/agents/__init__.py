"""Canonical Agent definitions, versioning and runtime services."""

# ruff: noqa: I001

from collections.abc import Mapping

from .models import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentDefinition,
    AgentExecutionSpec,
    AgentInstructions,
    AgentModelPolicy,
    AgentPolicyHooks,
    AgentProfile,
    AgentRevision,
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    AgentTeamDefinition,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevision,
    AgentTeamRevisionRef,
    AgentWorkspaceDefaults,
    CapabilityConstraint,
    InstructionSource,
    ModelFallbackPolicy,
    OrchestratorMapping,
    UnavailableMemberPolicy,
    new_agent_id,
    new_agent_run_id,
    new_team_id,
)
from .persistence import AGENT_REPOSITORY_SCHEMA_VERSION, JsonAgentRepository
from .repository import AgentRepository, InMemoryAgentRepository
from .runtime import AgentOrchestratorMapper, AgentRuntime, ReferenceOrchestratorMapper
from .routing_profile_runtime import DurableRoutingProfileAgentRuntime
from .service import AgentService
from .capability_turn import AgentCapabilityTurn, AgentCapabilityTurnResult

# AgentService must be initialized before control_plane imports. Real Forge/Hermes
# compatibility exercises import this package through execution adapters and otherwise
# expose a package-level cycle while control_plane resolves AgentService.
from .control_plane import (
    AGENT_COLLECTION,
    AGENT_COMMANDS,
    AGENT_RUN_COLLECTION,
    AGENT_TEAM_COLLECTION,
    AgentCommandHandlers,
    AgentExecutionEnvironment,
    AgentExecutionEnvironmentResolver,
    AgentResourceService,
    AgentRunResourceService,
    AgentTeamResourceService,
    register_agent_control_plane,
)
from .control_plane import _profile_from_json as _agent_profile_from_json
from .control_plane import _team_profile_from_json as _agent_team_profile_from_json
from .standards import (
    STANDARD_AGENT_IDS,
    STANDARD_AGENT_TEMPLATES,
    STANDARD_TEAM_IDS,
    STANDARD_TEAM_TEMPLATES,
    STARTER_CATALOG_SOURCE,
    STARTER_CATALOG_VERSION,
    STARTER_OWNER,
    STARTER_PLATFORM_RELEASE,
    CapabilityInventory,
    StandardAgentReadiness,
    StandardAgentTemplate,
    StandardTeamMemberTemplate,
    StandardTeamTemplate,
    StarterBootstrapResult,
    assess_standard_agent_capabilities,
    bootstrap_standard_agents,
    clone_standard_agent,
    clone_standard_team,
    ensure_standard_agent_capabilities,
    get_standard_agent_template,
    get_standard_team_template,
)
from .standards_control_plane import (
    SCOPED_STANDARD_AGENT_KEYS,
    SCOPED_STANDARD_TEAM_KEYS,
    STANDARD_AGENT_CATALOG_COLLECTION,
    STANDARD_AGENT_CATALOG_REF,
    STANDARD_AGENT_CONTROL_PLANE_COMMANDS,
    STANDARD_TEAM_CATALOG_COLLECTION,
    StandardAgentCatalogResourceService,
    StandardAgentCommandHandlers,
    StandardAgentTeamCatalogResourceService,
    register_standard_agent_control_plane,
)


def agent_profile_from_json(value: object) -> AgentProfile:
    """Parse the canonical Agent profile representation used by exports and Control Plane.

    Domain profiles represent an omitted description as an empty string. The northbound
    parser represents the same optional value as null/omitted, so normalize that one
    serialization detail before delegating to the shared canonical parser.
    """

    if isinstance(value, Mapping):
        normalized = dict(value)
        if normalized.get("description") == "":
            normalized["description"] = None
        return _agent_profile_from_json(normalized)
    return _agent_profile_from_json(value)


def agent_team_profile_from_json(value: object) -> AgentTeamProfile:
    """Parse the canonical Agent Team profile representation."""

    if isinstance(value, Mapping):
        normalized = dict(value)
        if normalized.get("description") == "":
            normalized["description"] = None
        return _agent_team_profile_from_json(normalized)
    return _agent_team_profile_from_json(value)


__all__ = [
    "AGENT_COLLECTION",
    "AGENT_COMMANDS",
    "AGENT_REPOSITORY_SCHEMA_VERSION",
    "AGENT_RUN_COLLECTION",
    "AGENT_TEAM_COLLECTION",
    "SCOPED_STANDARD_AGENT_KEYS",
    "SCOPED_STANDARD_TEAM_KEYS",
    "STANDARD_AGENT_CATALOG_COLLECTION",
    "STANDARD_AGENT_CATALOG_REF",
    "STANDARD_AGENT_CONTROL_PLANE_COMMANDS",
    "STANDARD_AGENT_IDS",
    "STANDARD_AGENT_TEMPLATES",
    "STANDARD_TEAM_CATALOG_COLLECTION",
    "STANDARD_TEAM_IDS",
    "STANDARD_TEAM_TEMPLATES",
    "STARTER_CATALOG_SOURCE",
    "STARTER_CATALOG_VERSION",
    "STARTER_OWNER",
    "STARTER_PLATFORM_RELEASE",
    "AgentCapabilityPolicy",
    "AgentCapabilityTurn",
    "AgentCapabilityTurnResult",
    "AgentCommandHandlers",
    "AgentDataAccess",
    "AgentDefinition",
    "AgentExecutionEnvironment",
    "AgentExecutionEnvironmentResolver",
    "AgentExecutionSpec",
    "AgentInstructions",
    "AgentModelPolicy",
    "AgentOrchestratorMapper",
    "AgentPolicyHooks",
    "AgentProfile",
    "AgentRepository",
    "AgentResourceService",
    "AgentRevision",
    "AgentRevisionRef",
    "AgentRunRecord",
    "AgentRunResourceService",
    "AgentRunStatus",
    "AgentRuntime",
    "AgentService",
    "AgentTeamDefinition",
    "AgentTeamMember",
    "AgentTeamProfile",
    "AgentTeamResourceService",
    "AgentTeamRevision",
    "AgentTeamRevisionRef",
    "AgentWorkspaceDefaults",
    "CapabilityConstraint",
    "CapabilityInventory",
    "DurableRoutingProfileAgentRuntime",
    "InMemoryAgentRepository",
    "InstructionSource",
    "JsonAgentRepository",
    "ModelFallbackPolicy",
    "OrchestratorMapping",
    "ReferenceOrchestratorMapper",
    "StandardAgentCatalogResourceService",
    "StandardAgentCommandHandlers",
    "StandardAgentReadiness",
    "StandardAgentTeamCatalogResourceService",
    "StandardAgentTemplate",
    "StandardTeamMemberTemplate",
    "StandardTeamTemplate",
    "StarterBootstrapResult",
    "UnavailableMemberPolicy",
    "agent_profile_from_json",
    "agent_team_profile_from_json",
    "assess_standard_agent_capabilities",
    "bootstrap_standard_agents",
    "clone_standard_agent",
    "clone_standard_team",
    "ensure_standard_agent_capabilities",
    "get_standard_agent_template",
    "get_standard_team_template",
    "new_agent_id",
    "new_agent_run_id",
    "new_team_id",
    "register_agent_control_plane",
    "register_standard_agent_control_plane",
]
