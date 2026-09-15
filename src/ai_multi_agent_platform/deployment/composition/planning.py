"""Durable Planning/Replanning composition for the public single-node profile."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentRepository
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator, SQLiteCoordinatorRepository
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
)
from ai_multi_agent_platform.planning import (
    JsonPlanningRepository,
    PlanningOrchestratorAdapter,
    PlanningService,
    PolicyAwarePlanningEnvironmentResolver,
    ReplanningEvidenceBridge,
    planning_command_handlers,
    planning_resource_services,
)
from ai_multi_agent_platform.planning.composition import (
    PlanningBindingCoordinator,
    PlanningOnlyLifecycleBackend,
    ReferencePlanningService,
)
from ai_multi_agent_platform.security import AuthorizationGate
from ai_multi_agent_platform.verification import SqliteVerificationService

from ..config import SingleNodeConfig
from ..reference_multi_agent import ReferenceMultiAgentPlanner


@dataclass(frozen=True, slots=True)
class PlanningBundle:
    """Durable Planning authorities plus the planning-only kernel adapter."""

    repository: JsonPlanningRepository
    kernel: PlatformKernel
    planning: PlanningService
    replanning: ReplanningEvidenceBridge


def build_planning(
    config: SingleNodeConfig,
    *,
    kernel: PlatformKernel,
    kernel_repository: SqliteKernelRepository,
    agents: AgentRepository,
    capabilities: CapabilityRegistry,
    models: ModelRegistry,
    authorization: AuthorizationGate,
    coordination: DurablePlanStepCoordinator,
    coordination_repository: SQLiteCoordinatorRepository,
    verification: SqliteVerificationService,
    telemetry: Telemetry,
    control_plane: ControlPlane,
) -> PlanningBundle:
    """Build durable Planning/Replanning and register its Control Plane surface."""

    repository = JsonPlanningRepository(config.database_dir / "planning.json")
    planning_kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(repository),
        lifecycle=PlanningOnlyLifecycleBackend(),
        repository=kernel_repository,
    )
    coordinator = PlanningBindingCoordinator(
        repository=repository,
        kernel=kernel,
        delegate=coordination,
    )
    environment = PolicyAwarePlanningEnvironmentResolver(
        agents=agents,
        capabilities=capabilities,
        authorization=authorization,
    )
    planning = ReferencePlanningService(
        planner=ReferenceMultiAgentPlanner(),
        repository=repository,
        kernel=planning_kernel,
        agents=agents,
        capabilities=capabilities,
        models=models,
        authorization=authorization,
        coordinator=coordinator,
        event_sink=_planning_event_sink(telemetry),
        environment_resolver=environment,
    )
    replanning = ReplanningEvidenceBridge(
        planning,
        coordination_repository=coordination_repository,
        verification_repository=verification,
        event_sink=_planning_event_sink(telemetry),
    )
    for collection, service in planning_resource_services(planning).items():
        control_plane.register_resource_service(collection, service)
    for command, handler in planning_command_handlers(planning).items():
        control_plane.register_command(command, handler)
    return PlanningBundle(
        repository=repository,
        kernel=planning_kernel,
        planning=planning,
        replanning=replanning,
    )


def _planning_event_sink(telemetry: Telemetry):
    """Project safe Planning transition evidence into the canonical timeline."""

    def emit(event_type: str, attributes: dict[str, object]) -> None:
        raw_task_id = attributes.get("task_id")
        task_id = raw_task_id if isinstance(raw_task_id, str) else None
        telemetry.timeline(
            event_name=event_type,
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(task_id=task_id, correlation_id=task_id),
            attributes=attributes,
        )

    return emit
