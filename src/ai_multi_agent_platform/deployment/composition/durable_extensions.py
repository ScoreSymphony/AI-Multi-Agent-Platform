"""Typed builders for durable Context, Learning and Handoff extensions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.context import (
    ContextEntryRole,
    ContextSourceAdapterBinding,
    ContextSourceType,
)
from ai_multi_agent_platform.context.lifecycle import ContextLifecycleSourceRequest
from ai_multi_agent_platform.kernel import EventSourcedTaskRepository
from ai_multi_agent_platform.learning.single_node import (
    SingleNodeLearningComposition,
    build_single_node_learning,
)
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    AutomationTemplateExporter,
    PlatformTemplateEnvironmentResolver,
    register_template_control_plane,
)

from ..context_operationalization import (
    SingleNodeContextComposition,
    install_single_node_context,
)
from ..egress_bindings import EgressDeploymentBindings
from ..handoff_composition import (
    HandoffDeploymentComposition,
    build_single_node_handoff_composition,
)
from ..reference_multi_agent import ReferenceIncomingHandoffContextAdapter
from .durable import PlanningBundle

if TYPE_CHECKING:
    from ai_multi_agent_platform.connectors import ConnectorRegistry

    from ..single_node import SingleNodeDeployment as BaseSingleNodeDeployment


@dataclass(frozen=True, slots=True)
class DurableExtensionBundle:
    """Context, Learning and Handoff owner-domain extensions."""

    context: SingleNodeContextComposition
    learning: SingleNodeLearningComposition
    handoffs: HandoffDeploymentComposition


def build_durable_extensions(
    base: BaseSingleNodeDeployment,
    *,
    egress: EgressDeploymentBindings,
    planning: PlanningBundle,
) -> DurableExtensionBundle:
    """Build Context, Learning and Handoffs after base runtime and Planning exist."""

    context = install_single_node_context(base, egress=egress)
    learning = build_single_node_learning(
        database_dir=base.config.database_dir,
        agents=base.agents,
        routing_profiles=base.routing_profiles,
        evaluation=base.evaluation,
        verification=base.verification,
        approval_gate=base.approval_gate,
        kernel=base.kernel,
        planning=planning.service,
        telemetry=base.telemetry,
        skills=context.skills,
        research=context.research,
    )
    learning.register_control_plane(base.control_plane)

    handoffs = build_single_node_handoff_composition(
        database_dir=base.config.database_dir,
        control_plane=base.control_plane,
        agents=base.agents.repository,
        agent_runtime=base.agent_runtime,
        coordinator=base.coordination_repository,
        tasks=EventSourcedTaskRepository(base.kernel_repository),
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
    _register_handoff_context_source(base, context, handoffs)
    return DurableExtensionBundle(context=context, learning=learning, handoffs=handoffs)


def _register_handoff_context_source(
    base: BaseSingleNodeDeployment,
    context: SingleNodeContextComposition,
    handoffs: HandoffDeploymentComposition,
) -> None:
    incoming_handoffs = ReferenceIncomingHandoffContextAdapter(
        handoffs,
        coordinator=base.coordination_repository,
        kernel=base.kernel,
    )

    def reference_binding_factory(
        source: ContextLifecycleSourceRequest,
    ) -> tuple[ContextSourceAdapterBinding, ...]:
        if source.step_id is None:
            return ()
        return (
            ContextSourceAdapterBinding(
                adapter=incoming_handoffs,
                source_type=ContextSourceType.AGENT_HANDOFF,
                source_id=f"run:{source.run_id}:incoming-handoffs",
                role=ContextEntryRole.CONTEXT,
                project_id=source.project_id,
                workspace_id=source.workspace_id,
            ),
        )

    context.lifecycle.register_source_binding_factory(reference_binding_factory)


def register_durable_template_environment(
    base: BaseSingleNodeDeployment,
    connector_registry: ConnectorRegistry,
) -> None:
    """Refresh template environment resolution with durable connector inventory."""

    environment = PlatformTemplateEnvironmentResolver(
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
        environment_resolver=environment,
        agent_exporter=AgentTemplateExporter(base.agents, base.templates.templates),
        automation_exporter=AutomationTemplateExporter(
            base.control_plane.automation_service,
            base.templates.templates,
        ),
    )


__all__ = [
    "DurableExtensionBundle",
    "build_durable_extensions",
    "register_durable_template_environment",
]
