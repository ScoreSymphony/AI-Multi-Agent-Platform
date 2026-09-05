"""Production composition helpers for canonical portability workflows."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.evaluation.service import EvaluationService
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileRepository,
    AuthorizationPolicyProfileService,
)
from ai_multi_agent_platform.templates import TemplateRepository

from .agent_codecs import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    register_agent_portability_codecs,
    snapshot_agent,
    snapshot_agent_team,
)
from .agent_import import AgentImportMutationHandler, AgentTeamImportMutationHandler
from .dependencies import parse_resource_dependency
from .evaluation_codecs import (
    EVALUATION_FIXTURE_RESOURCE_TYPE,
    EVALUATION_SUITE_RESOURCE_TYPE,
    register_evaluation_suite_portability_codec,
)
from .evaluation_import import EvaluationSuiteImportMutationHandler
from .executor import ImportExecutor, ImportMutationRegistry
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .planner import ImportPreviewService, ImportSecurityFinding
from .policy_profile_codecs import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    inspect_authorization_policy_profile_import,
    register_authorization_policy_profile_portability_codec,
    snapshot_authorization_policy_profile,
)
from .policy_profile_import import AuthorizationPolicyProfileImportMutationHandler
from .project_codecs import PROJECT_RESOURCE_TYPE, register_project_portability_codec
from .project_import import ProjectDependencyAudit, ProjectImportMutationHandler
from .registry import ResourceSerializerRegistry
from .template_codecs import (
    TEMPLATE_RESOURCE_TYPE,
    register_template_portability_codec,
    snapshot_template,
)
from .template_import import TemplateImportMutationHandler
from .workflow import ExportSourceRegistry, PortabilityWorkflowService


def build_agent_portability_workflow(
    *,
    agents: AgentRepository,
    models: ModelRegistry,
    scopes: ScopeStore,
    platform_version: str,
    templates: TemplateRepository | None = None,
    evaluation: EvaluationService | None = None,
    evaluation_fixture_exists: Callable[[str], bool] | None = None,
    policy_profiles: AuthorizationPolicyProfileRepository | None = None,
    policy_profile_service: AuthorizationPolicyProfileService | None = None,
    policy_profile_import_context: AuthorizationPolicyProfileCallContext | None = None,
    policy_profile_target_owner: OwnerRef | None = None,
    source_instance_id: str | None = None,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
    project_dependency_audit: ProjectDependencyAudit | None = None,
    additional_resource_exists: Callable[[str, str], bool] | None = None,
) -> PortabilityWorkflowService:
    """Compose production-safe portability against supplied canonical stores.

    Agent, Agent Team and Project are always available. Template and EvaluationSuite
    portability are enabled only when their owning-domain repositories/services are
    supplied. Authorization-policy portability is enabled only when the canonical #310
    repository, canonical lifecycle service, explicit import context and explicit
    destination owner are supplied together. Imported policy profiles are therefore
    materialized through the normal security domain and never gain assignments or effective
    authority as an import side effect.

    Project rollback deliberately fails closed unless the caller supplies a cross-domain
    dependency audit that can prove removal is safe. Resource domains not owned directly by
    this composition, such as Organization, Team or Node, can expose a synchronous
    canonical existence view through ``additional_resource_exists``. Without that view,
    those dependencies remain unavailable and import preview fails closed rather than
    making optimistic assumptions about target state.
    """

    policy_parts = (
        policy_profiles,
        policy_profile_service,
        policy_profile_import_context,
        policy_profile_target_owner,
    )
    if any(item is not None for item in policy_parts) and not all(
        item is not None for item in policy_parts
    ):
        raise ValueError(
            "policy profile portability requires repository, canonical service, "
            "import context and target owner"
        )

    serializers = ResourceSerializerRegistry()
    register_agent_portability_codecs(
        serializers,
        agent_id_policy=id_policy,
        team_id_policy=id_policy,
    )
    register_project_portability_codec(serializers, id_policy=id_policy)
    if templates is not None:
        register_template_portability_codec(serializers, id_policy=id_policy)
    if evaluation is not None:
        register_evaluation_suite_portability_codec(serializers)
    if policy_profiles is not None:
        register_authorization_policy_profile_portability_codec(
            serializers,
            id_policy=id_policy,
        )

    export_sources = ExportSourceRegistry()

    async def load_agent(resource_id: str) -> object:
        return snapshot_agent(agents, resource_id)

    async def load_team(resource_id: str) -> object:
        return snapshot_agent_team(agents, resource_id)

    async def load_project(resource_id: str) -> object:
        return scopes.get_project(resource_id)

    export_sources.register(AGENT_RESOURCE_TYPE, load_agent)
    export_sources.register(AGENT_TEAM_RESOURCE_TYPE, load_team)
    export_sources.register(PROJECT_RESOURCE_TYPE, load_project)
    if templates is not None:

        async def load_template(resource_id: str) -> object:
            return snapshot_template(templates, resource_id)

        export_sources.register(TEMPLATE_RESOURCE_TYPE, load_template)
    if evaluation is not None:

        async def load_evaluation_suite(resource_id: str) -> object:
            return evaluation.get_suite(resource_id)

        export_sources.register(EVALUATION_SUITE_RESOURCE_TYPE, load_evaluation_suite)
    if policy_profiles is not None:

        async def load_policy_profile(resource_id: str) -> object:
            return snapshot_authorization_policy_profile(policy_profiles, resource_id)

        export_sources.register(
            AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
            load_policy_profile,
        )

    mutations = ImportMutationRegistry()
    mutations.register(AgentImportMutationHandler(agents))
    mutations.register(AgentTeamImportMutationHandler(agents))
    mutations.register(
        ProjectImportMutationHandler(
            scopes,
            dependency_audit=project_dependency_audit,
        )
    )
    if templates is not None:
        mutations.register(TemplateImportMutationHandler(templates))
    if evaluation is not None:
        mutations.register(EvaluationSuiteImportMutationHandler(evaluation))
    if (
        policy_profiles is not None
        and policy_profile_service is not None
        and policy_profile_import_context is not None
        and policy_profile_target_owner is not None
    ):
        mutations.register(
            AuthorizationPolicyProfileImportMutationHandler(
                policy_profile_service,
                policy_profiles,
                import_context=policy_profile_import_context,
                target_owner_ref=policy_profile_target_owner,
            )
        )

    def resource_exists(resource_type: str, resource_id: str) -> bool:
        if resource_type == AGENT_RESOURCE_TYPE:
            return _canonical_exists(lambda: agents.get_agent(resource_id))
        if resource_type == AGENT_TEAM_RESOURCE_TYPE:
            return _canonical_exists(lambda: agents.get_team(resource_id))
        if resource_type == TEMPLATE_RESOURCE_TYPE and templates is not None:
            return _canonical_exists(lambda: templates.get_template(resource_id))
        if (
            resource_type == AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE
            and policy_profiles is not None
        ):
            return _canonical_exists(lambda: policy_profiles.get_profile(resource_id))
        if resource_type == PROJECT_RESOURCE_TYPE:
            return _canonical_exists(lambda: scopes.get_project(resource_id))
        if resource_type == EVALUATION_SUITE_RESOURCE_TYPE and evaluation is not None:
            return _canonical_exists(lambda: evaluation.get_suite(resource_id))
        if resource_type == EVALUATION_FIXTURE_RESOURCE_TYPE:
            return (
                False
                if evaluation_fixture_exists is None
                else evaluation_fixture_exists(resource_id)
            )
        if resource_type == "workspace":
            return _canonical_exists(lambda: scopes.get_workspace(resource_id))
        if additional_resource_exists is not None:
            return additional_resource_exists(resource_type, resource_id)
        return False

    def dependency_available(requirement: DependencyRequirement) -> bool:
        if requirement.kind is DependencyKind.RESOURCE:
            reference = parse_resource_dependency(requirement)
            return resource_exists(reference.resource_type, reference.resource_id)
        if requirement.kind is DependencyKind.MODEL:
            return _canonical_exists(lambda: models.get_model(requirement.identifier))
        return False

    def inspect_security(
        resource: PortableResource,
        target_id: str,
    ) -> tuple[ImportSecurityFinding, ...]:
        if resource.resource_type == AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE:
            return inspect_authorization_policy_profile_import(resource, target_id)
        return ()

    preview = ImportPreviewService(
        resource_exists=resource_exists,
        dependency_available=dependency_available,
        security_inspector=inspect_security if policy_profiles is not None else None,
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
