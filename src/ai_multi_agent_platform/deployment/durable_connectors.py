"""Durable Connector composition for the normal single-node/server runtime."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.observability import (
    EgressTelemetryAuditSink,
    FailureComponent,
    InMemoryExporter,
    Telemetry,
    TelemetryContext,
)
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.planning import (
    DeterministicReferencePlanner,
    JsonPlanningRepository,
    PlanningOrchestratorAdapter,
    PlanningService,
    PolicyAwarePlanningEnvironmentResolver,
    planning_command_handlers,
    planning_resource_services,
)
from ai_multi_agent_platform.planning.composition import (
    PlanningBindingCoordinator,
    PlanningOnlyLifecycleBackend,
    ReferencePlanningService,
)
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.repositories.connector_bootstrap import (
    connector_repository_discovery_resolver,
)
from ai_multi_agent_platform.security import build_durable_egress_runtime
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    AutomationTemplateExporter,
    PlatformTemplateEnvironmentResolver,
    register_template_control_plane,
)

from .config import SingleNodeConfig
from .context_operationalization import (
    SingleNodeContextComposition,
    install_single_node_context,
)
from .egress_bindings import EgressDeploymentBindings
from .handoff_composition import (
    HandoffDeploymentComposition,
    build_single_node_handoff_composition,
)
from .single_node import (
    SingleNodeDeployment as BaseSingleNodeDeployment,
)
from .single_node import (
    SingleNodeSmokeResult,
)
from .single_node import (
    build_single_node_deployment as _build_base_single_node_deployment,
)


@dataclass(slots=True)
class SingleNodeDeployment(BaseSingleNodeDeployment):
    """Normal single-node deployment with durable Connector, Planning,
    canonical Context, Egress and Handoff state.
    """

    connector_repository: SqliteConnectorRepository
    connector_registry: ConnectorRegistry
    connectors: ConnectorService
    planning_repository: JsonPlanningRepository
    planning_kernel: PlatformKernel
    planning: PlanningService
    egress: EgressDeploymentBindings
    context: SingleNodeContextComposition
    handoffs: HandoffDeploymentComposition


def build_single_node_deployment(
    config: SingleNodeConfig,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter] = (),
    secret_provider: SecretProvider | None = None,
    accounting_service: AccountingService | None = None,
    observability_exporter: InMemoryExporter | None = None,
    distributed_runtime: DistributedRuntime | None = None,
    enable_distributed_execution: bool = False,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
) -> SingleNodeDeployment:
    """Build the normal durable single-node profile.

    The lower-level ``deployment.single_node`` composition remains usable by focused tests and
    explicitly minimal/ephemeral profiles. Public deployment/server composition comes through this
    wrapper so Connector Definitions, Connections, planning proposals, canonical Context Bundle/
    Run-binding evidence, one durable #591 egress policy runtime and Agent Handoffs are durable
    across process restarts. Context execution remains fully local by default and introduces no
    hosted RAG/model dependency.
    """

    # Preserve the base deployment's canonical configuration error boundary before the Connector
    # repository touches a path under the data root. This keeps invalid persistence roots from
    # leaking backend-specific OSError subclasses through the public single-node builder.
    config.prepare_directories()
    connector_repository = SqliteConnectorRepository(config.database_dir / "connectors.sqlite3")
    connector_registry = ConnectorRegistry()
    effective_repository_resolver = repository_discovery_resolver or (
        connector_repository_discovery_resolver(connector_repository, connector_registry)
    )

    base = _build_base_single_node_deployment(
        config,
        onboarding_model_adapters=onboarding_model_adapters,
        secret_provider=secret_provider,
        accounting_service=accounting_service,
        observability_exporter=observability_exporter,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=effective_repository_resolver,
    )

    # #591 owns one durable disclosure-policy runtime for the public process. Rebind the already
    # constructed ModelRuntime rather than replacing it so Conversation/Onboarding/Lifecycle
    # references retain object identity while both routing preselection and provider invocation see
    # the shared gate.
    egress_runtime = build_durable_egress_runtime(
        config.database_dir / "egress-profiles.json",
        authorization=base.authorization,
        approval_gate=base.approval_gate,
        audit_sink=EgressTelemetryAuditSink(base.telemetry),
    )
    egress = EgressDeploymentBindings(egress_runtime)
    base.model_runtime.egress_gate = egress.runtime.gate

    connectors = egress.connector_service(
        connector_repository,
        connector_registry,
        authorization_gate=base.approval_gate,
    )
    register_connector_control_plane(base.control_plane, connectors)
    egress.register_control_plane(base.control_plane)

    planning_repository = JsonPlanningRepository(config.database_dir / "planning.json")
    planning_kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(planning_repository),
        lifecycle=PlanningOnlyLifecycleBackend(),
        repository=base.kernel_repository,
    )
    planning_coordinator = PlanningBindingCoordinator(
        repository=planning_repository,
        kernel=base.kernel,
        delegate=base.coordination,
    )
    planning_environment = PolicyAwarePlanningEnvironmentResolver(
        agents=base.agents.repository,
        capabilities=base.capabilities,
        authorization=base.approval_gate,
    )
    planning = ReferencePlanningService(
        planner=DeterministicReferencePlanner(),
        repository=planning_repository,
        kernel=planning_kernel,
        agents=base.agents.repository,
        capabilities=base.capabilities,
        models=base.models,
        authorization=base.approval_gate,
        coordinator=planning_coordinator,
        event_sink=_planning_event_sink(base.telemetry),
        environment_resolver=planning_environment,
    )
    for collection, service in planning_resource_services(planning).items():
        base.control_plane.register_resource_service(collection, service)
    for command, handler in planning_command_handlers(planning).items():
        base.control_plane.register_command(command, handler)

    # Install canonical Context only after the authoritative Task/Run, Coordination, Repository,
    # Agent and model components are available. The installer replaces the Agent-bound lifecycle
    # seam on the same kernel object, so existing services use the canonical Context path. Passing
    # the shared #591 bindings prevents provider-bound rendering/capabilities from creating private
    # policy gates.
    context = install_single_node_context(base, egress=egress)

    handoffs = build_single_node_handoff_composition(
        database_dir=config.database_dir,
        control_plane=base.control_plane,
        agents=base.agents.repository,
        agent_runtime=base.agent_runtime,
        coordinator=base.coordination_repository,
        authorization=base.approval_gate.provider,
        verification=base.verification_runtime.evidence,
        telemetry=base.telemetry,
        research_repository=context.research_repository,
        skill_repository=context.skills_repository,
        context_bundle_repository=context.bundles,
        context_binding_repository=context.run_bindings,
        egress_gate=egress.runtime.gate,
        model_runtime=base.model_runtime,
    )

    # The public deployment now has an authoritative canonical Connector inventory. Rebind the
    # Template surface to a resolver that includes exactly those ConnectorDefinition IDs instead
    # of leaving connector requirements permanently fail-closed. The callback closes over the
    # live registry so later provider registration/removal is reflected immediately in preview.
    template_environment = PlatformTemplateEnvironmentResolver(
        workspaces=base.workspaces,
        capabilities=lambda: (
            capability.capability_id
            for capability in base.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        capability_versions=lambda: (
            (capability.capability_id, capability.version)
            for capability in base.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        connectors=lambda: (definition.id for definition in connector_registry.definitions()),
        model_policies=lambda: (
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision).canonical_ref
            for definition in base.routing_profile_repository.list_definitions()
            if definition.enabled
        ),
        grantable_permissions=lambda context: (
            action.value
            for action in base.authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
        platform_version=__version__,
    )
    register_template_control_plane(
        base.control_plane,
        base.templates,
        environment_resolver=template_environment,
        agent_exporter=AgentTemplateExporter(base.agents, base.templates.templates),
        automation_exporter=AutomationTemplateExporter(
            base.control_plane.automation_service,
            base.templates.templates,
        ),
    )

    base_values: dict[str, Any] = {
        field.name: getattr(base, field.name) for field in fields(BaseSingleNodeDeployment)
    }
    return SingleNodeDeployment(
        **base_values,
        connector_repository=connector_repository,
        connector_registry=connector_registry,
        connectors=connectors,
        planning_repository=planning_repository,
        planning_kernel=planning_kernel,
        planning=planning,
        egress=egress,
        context=context,
        handoffs=handoffs,
    )


def _planning_event_sink(
    telemetry: Telemetry,
) -> Callable[[str, dict[str, JsonValue]], None]:
    """Project safe #439 transition evidence into the canonical observability timeline."""

    def emit(event_type: str, attributes: dict[str, JsonValue]) -> None:
        raw_task_id = attributes.get("task_id")
        task_id = raw_task_id if isinstance(raw_task_id, str) else None
        telemetry.timeline(
            event_name=event_type,
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(
                task_id=task_id,
                correlation_id=task_id,
            ),
            attributes=attributes,
        )

    return emit


__all__ = [
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "build_single_node_deployment",
]
