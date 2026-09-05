"""Production composition helpers for canonical portability workflows."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.models import ModelRegistry

from .agent_codecs import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    register_agent_portability_codecs,
    snapshot_agent,
    snapshot_agent_team,
)
from .agent_import import AgentImportMutationHandler, AgentTeamImportMutationHandler
from .dependencies import parse_resource_dependency
from .executor import ImportExecutor, ImportMutationRegistry
from .models import DependencyKind, DependencyRequirement, IdPolicy
from .planner import ImportPreviewService
from .registry import ResourceSerializerRegistry
from .workflow import ExportSourceRegistry, PortabilityWorkflowService


def build_agent_portability_workflow(
    *,
    agents: AgentRepository,
    models: ModelRegistry,
    scopes: ScopeStore,
    platform_version: str,
    source_instance_id: str | None = None,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> PortabilityWorkflowService:
    """Compose production-safe Agent/Team portability against canonical stores.

    Only dependencies whose canonical destination registries are supplied here are
    considered available. Capability, plugin, connector and secret requirements stay
    unavailable until their respective production registries are explicitly composed.
    This prevents preview from making optimistic assumptions about target state.
    """

    serializers = ResourceSerializerRegistry()
    register_agent_portability_codecs(
        serializers,
        agent_id_policy=id_policy,
        team_id_policy=id_policy,
    )

    export_sources = ExportSourceRegistry()

    async def load_agent(resource_id: str) -> object:
        return snapshot_agent(agents, resource_id)

    async def load_team(resource_id: str) -> object:
        return snapshot_agent_team(agents, resource_id)

    export_sources.register(AGENT_RESOURCE_TYPE, load_agent)
    export_sources.register(AGENT_TEAM_RESOURCE_TYPE, load_team)

    mutations = ImportMutationRegistry()
    mutations.register(AgentImportMutationHandler(agents))
    mutations.register(AgentTeamImportMutationHandler(agents))

    def resource_exists(resource_type: str, resource_id: str) -> bool:
        if resource_type == AGENT_RESOURCE_TYPE:
            return _canonical_exists(lambda: agents.get_agent(resource_id))
        if resource_type == AGENT_TEAM_RESOURCE_TYPE:
            return _canonical_exists(lambda: agents.get_team(resource_id))
        if resource_type == "project":
            return _canonical_exists(lambda: scopes.get_project(resource_id))
        if resource_type == "workspace":
            return _canonical_exists(lambda: scopes.get_workspace(resource_id))
        return False

    def dependency_available(requirement: DependencyRequirement) -> bool:
        if requirement.kind is DependencyKind.RESOURCE:
            reference = parse_resource_dependency(requirement)
            return resource_exists(reference.resource_type, reference.resource_id)
        if requirement.kind is DependencyKind.MODEL:
            return _canonical_exists(lambda: models.get_model(requirement.identifier))
        return False

    preview = ImportPreviewService(
        resource_exists=resource_exists,
        dependency_available=dependency_available,
    )
    return PortabilityWorkflowService(
        serializers=serializers,
        export_sources=export_sources,
        preview_service=preview,
        executor=ImportExecutor(serializers, mutations),
        platform_version=platform_version,
        source_instance_id=source_instance_id,
    )


def _canonical_exists(loader: Callable[[], object]) -> bool:
    try:
        loader()
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return False
        raise
    return True


__all__ = ["build_agent_portability_workflow"]
