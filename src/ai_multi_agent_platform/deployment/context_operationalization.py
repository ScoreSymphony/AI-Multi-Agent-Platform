"""Operational #590 Context Bundle composition for the public single-node runtime.

The lower-level deployment builder intentionally stays reusable for focused embeddings. The normal
server-facing composition installs this module after its durable domain repositories are available
so every canonical Agent-bound Run crosses the Context Bundle boundary before model execution.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from ai_multi_agent_platform.agents import AgentCapabilityTurn, AgentRepository, AgentRunStatus
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilitySpec,
    ExternalEffectReconciler,
    ExternalEffectRecoveryCoordinator,
    SQLiteExternalEffectRecoveryRepository,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.capabilities.recovery_control_plane import (
    register_external_effect_recovery_control_plane,
)
from ai_multi_agent_platform.context.bindings import (
    ContextRunBindingRepository,
    JsonContextRunBindingRepository,
)
from ai_multi_agent_platform.context.classification import effective_context_bundle_classification
from ai_multi_agent_platform.context.control_plane import register_context_control_plane
from ai_multi_agent_platform.context.kernel_plan_step import (
    KernelFallbackPlanStepContextSourceAdapter,
)
from ai_multi_agent_platform.context.lifecycle import (
    CanonicalContextAgentLifecycleBackend,
    ContextLifecycleSourceRequest,
    _bind_task_project_scope,
)
from ai_multi_agent_platform.context.models import ContextEntryRole, ContextSourceType
from ai_multi_agent_platform.context.operational import (
    ContextRoutingPolicy,
    ModelRegistryContextEgressTargetResolver,
    OperationalContextBoundAgentRuntime,
)
from ai_multi_agent_platform.context.persistence import JsonContextBundleRepository
from ai_multi_agent_platform.context.reconciliation import (
    ContextBindingReconciliationReport,
    reconcile_context_run_bindings,
)
from ai_multi_agent_platform.context.resolver import (
    ContextAssemblyService,
    ContextBundleRepository,
    ContextResolver,
)
from ai_multi_agent_platform.context.source_adapters import (
    AgentContextSourceAdapter,
    ContextSourceAdapterBinding,
    FileArtifactResultContextSourceAdapter,
    KnowledgeContextSourceAdapter,
    MemoryContextSourceAdapter,
    OperationalContextAssemblyService,
    RepositoryContextSourceAdapter,
    ResearchEvidenceContextSourceAdapter,
    SkillBundleContextSourceAdapter,
    TaskContextSourceAdapter,
)
from ai_multi_agent_platform.context.verification_source import VerificationContextSourceAdapter
from ai_multi_agent_platform.context.visibility import AuthorizationContextEntryVisibilityResolver
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.data import LocalKnowledgeProvider, LocalMemoryProvider
from ai_multi_agent_platform.kernel import EventSourcedRunRepository, EventSourcedTaskRepository
from ai_multi_agent_platform.observability import (
    ObservabilityExternalEffectRecoveryObserver,
    ObservabilityInvocationObserver,
    TraceHierarchy,
)
from ai_multi_agent_platform.research import ResearchService, SqliteResearchRepository
from ai_multi_agent_platform.security import (
    AuthorizedDataFileProvider,
    AuthorizedDataKnowledgeProvider,
    AuthorizedDataMemoryProvider,
    AuthorizedLifecycleBackend,
)
from ai_multi_agent_platform.skills import (
    JsonSkillRepository,
    SkillService,
    register_skill_control_plane,
)

from .context_verification import CanonicalVerificationContextClassificationResolver
from .egress_bindings import EgressDeploymentBindings
from .external_effect_recovery import ExternalEffectStartupRecovery
from .startup_recovery import StartupRecoveryExtension

if TYPE_CHECKING:
    from ai_multi_agent_platform.deployment.single_node import (
        SingleNodeDeployment as BaseDeployment,
    )


CONTEXT_OUTPUT_RESERVE_TOKENS = 2_048


class _TaskProjectScopeLifecycleBackend(LifecycleBackend):
    """Resolve canonical Task Project scope before the lifecycle authorization boundary."""

    def __init__(
        self,
        inner: LifecycleBackend,
        tasks: EventSourcedTaskRepository,
    ) -> None:
        self._inner = inner
        self._tasks = tasks

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        task = await self._tasks.get_task(request.context.correlation_id)
        context = _bind_task_project_scope(request.context, task.task.project_id)
        return await self._inner.start(replace(request, context=context))

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        return await self._inner.get(run_id, context)

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        return await self._inner.cancel(run_id, context)


def _bound_capability_classification(
    request: CapabilityInvocation,
    *,
    agents: AgentRepository,
    bundles: ContextBundleRepository,
    run_bindings: ContextRunBindingRepository,
) -> DataClassification:
    agent_run_id = request.trace.agent_run_id
    if agent_run_id is None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress requires the exact invoking AgentRun identity",
            details={
                "run_id": request.trace.run_id,
                "agent_id": request.trace.agent_id,
            },
        )
    try:
        agent_run = agents.get_agent_run(agent_run_id)
    except KeyError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress references a missing invoking AgentRun",
            details={"agent_run_id": agent_run_id},
        ) from exc
    if (
        agent_run.run_id != request.trace.run_id
        or agent_run.task_id != request.trace.task_id
        or agent_run.agent.agent_id != request.trace.agent_id
        or agent_run.status is not AgentRunStatus.RUNNING
    ):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress AgentRun does not match the invocation trace",
            details={"agent_run_id": agent_run_id},
        )
    try:
        binding = run_bindings.get(agent_run_id)
    except KeyError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress requires the invoking AgentRun Context binding",
            details={"agent_run_id": agent_run_id},
        ) from exc
    if (
        binding.run_id != request.trace.run_id
        or binding.task_id != request.trace.task_id
        or binding.agent_id != request.trace.agent_id
        or binding.agent_revision != agent_run.agent.revision
    ):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress binding does not match the invoking AgentRun",
            details={"agent_run_id": agent_run_id},
        )
    try:
        bundle = bundles.get(binding.context_bundle_id)
    except KeyError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress binding references a missing Context Bundle",
            details={"context_bundle_id": binding.context_bundle_id},
        ) from exc
    if (
        bundle.context_bundle_id != binding.context_bundle_id
        or bundle.digest != binding.context_bundle_digest
        or bundle.run_id != binding.run_id
        or bundle.task_id != binding.task_id
        or bundle.agent_id != binding.agent_id
        or bundle.agent_revision != binding.agent_revision
    ):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Context capability egress bundle does not match the invoking AgentRun binding",
            details={
                "agent_run_id": agent_run_id,
                "context_bundle_id": binding.context_bundle_id,
            },
        )
    return effective_context_bundle_classification(bundle)


@dataclass(slots=True)
class SingleNodeContextComposition:
    """Durable context-related state exposed by the public SingleNodeDeployment."""

    bundles: JsonContextBundleRepository
    run_bindings: JsonContextRunBindingRepository
    assembly: OperationalContextAssemblyService
    runtime: OperationalContextBoundAgentRuntime
    lifecycle: CanonicalContextAgentLifecycleBackend
    memory: LocalMemoryProvider
    knowledge: LocalKnowledgeProvider
    skills_repository: JsonSkillRepository
    skills: SkillService
    research_repository: SqliteResearchRepository
    research: ResearchService
    reconciliation: ContextBindingReconciliationReport
    external_effect_recovery: ExternalEffectRecoveryCoordinator | None = None
    startup_recovery_extensions: tuple[StartupRecoveryExtension, ...] = ()


def install_single_node_context(
    base: BaseDeployment,
    *,
    egress: EgressDeploymentBindings | None = None,
) -> SingleNodeContextComposition:
    """Install the canonical Context Bundle path into a built single-node deployment."""

    database_dir = base.config.database_dir
    tasks = EventSourcedTaskRepository(base.kernel_repository)
    runs = EventSourcedRunRepository(base.kernel_repository)

    bundles = JsonContextBundleRepository(database_dir / "context-bundles.json")
    run_bindings = JsonContextRunBindingRepository(database_dir / "context-run-bindings.json")
    canonical_assembly = ContextAssemblyService(ContextResolver(base.authorization), bundles)
    assembly = OperationalContextAssemblyService(canonical_assembly)
    context_runtime = OperationalContextBoundAgentRuntime(
        base.agent_runtime,
        bundle_repository=bundles,
        binding_repository=run_bindings,
        egress_gate=None if egress is None else egress.runtime.gate,
        target_resolver=ModelRegistryContextEgressTargetResolver(base.models),
        model_runtime=base.model_runtime if egress is not None else None,
        routing_policy=ContextRoutingPolicy(
            output_reserve_tokens=CONTEXT_OUTPUT_RESERVE_TOKENS,
        ),
    )

    protected_files = AuthorizedDataFileProvider(base.files, base.approval_gate)
    memory = LocalMemoryProvider(database_dir / "memory.sqlite3")
    knowledge = LocalKnowledgeProvider(database_dir / "knowledge.sqlite3")
    protected_memory = AuthorizedDataMemoryProvider(memory, base.approval_gate)
    protected_knowledge = AuthorizedDataKnowledgeProvider(knowledge, base.approval_gate)

    skills_repository = JsonSkillRepository(database_dir / "skills.json")
    skills = SkillService(skills_repository)
    research = base.research
    research_repository = cast(SqliteResearchRepository, research.repository)

    task_adapter = TaskContextSourceAdapter(tasks)
    agent_adapter = AgentContextSourceAdapter(base.agents)
    plan_adapter = KernelFallbackPlanStepContextSourceAdapter(
        base.coordination_repository,
        tasks=tasks,
        events=base.kernel_repository,
        runs=runs,
    )
    skill_adapter = SkillBundleContextSourceAdapter(skills_repository)
    research_adapter = ResearchEvidenceContextSourceAdapter(research)
    verification_adapter = VerificationContextSourceAdapter(
        base.verification,
        classification_resolver=CanonicalVerificationContextClassificationResolver(
            protected_files,
            evidence=base.verification_runtime.evidence,
            agents=base.agents.repository,
            bundles=bundles,
            run_bindings=run_bindings,
        ),
    )
    repository_adapter = RepositoryContextSourceAdapter(
        base.repository_provenance,
        repositories=base.repositories,
        tasks=tasks,
    )
    file_adapter = FileArtifactResultContextSourceAdapter(protected_files, tasks, runs)
    memory_adapter = MemoryContextSourceAdapter(protected_memory)
    knowledge_adapter = KnowledgeContextSourceAdapter(protected_knowledge, base.agents)

    def binding_factory(
        source: ContextLifecycleSourceRequest,
    ) -> tuple[ContextSourceAdapterBinding, ...]:
        bindings: list[ContextSourceAdapterBinding] = [
            ContextSourceAdapterBinding(
                adapter=task_adapter,
                source_type=ContextSourceType.TASK,
                source_id=source.task_id,
                role=ContextEntryRole.CONTEXT,
                mandatory=True,
                record_absence=True,
                project_id=source.project_id,
            ),
            ContextSourceAdapterBinding(
                adapter=agent_adapter,
                source_type=ContextSourceType.AGENT,
                source_id=source.agent_id,
                role=ContextEntryRole.INSTRUCTION,
                mandatory=True,
                record_absence=True,
                project_id=source.project_id,
                workspace_id=source.workspace_id,
            ),
        ]
        if source.step_id is not None:
            bindings.append(
                ContextSourceAdapterBinding(
                    adapter=plan_adapter,
                    source_type=ContextSourceType.PLAN_STEP,
                    source_id=source.step_id,
                    role=ContextEntryRole.CONTEXT,
                    mandatory=True,
                    record_absence=True,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                )
            )
        bindings.extend(
            (
                ContextSourceAdapterBinding(
                    adapter=skill_adapter,
                    source_type=ContextSourceType.SKILL,
                    source_id=f"run:{source.run_id}:skill-bundle",
                    role=ContextEntryRole.INSTRUCTION,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    adapter=research_adapter,
                    source_type=ContextSourceType.RESEARCH_EVIDENCE,
                    source_id=f"run:{source.run_id}:research",
                    role=ContextEntryRole.EVIDENCE,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    adapter=verification_adapter,
                    source_type=ContextSourceType.VERIFICATION,
                    source_id=f"task:{source.task_id}:verification",
                    role=ContextEntryRole.EVIDENCE,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    adapter=repository_adapter,
                    source_type=ContextSourceType.REPOSITORY,
                    source_id=f"run:{source.run_id}:repositories",
                    role=ContextEntryRole.CONTEXT,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    adapter=file_adapter,
                    source_type=ContextSourceType.FILE,
                    source_id=f"run:{source.run_id}:files-artifacts-results",
                    role=ContextEntryRole.CONTEXT,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    adapter=memory_adapter,
                    source_type=ContextSourceType.MEMORY,
                    source_id=f"run:{source.run_id}:memory",
                    role=ContextEntryRole.CONTEXT,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    adapter=knowledge_adapter,
                    source_type=ContextSourceType.KNOWLEDGE,
                    source_id=f"run:{source.run_id}:knowledge",
                    role=ContextEntryRole.CONTEXT,
                    project_id=source.project_id,
                    workspace_id=source.workspace_id,
                ),
            )
        )
        return tuple(bindings)

    def skill_bundle_resolver(
        source: ContextLifecycleSourceRequest,
    ) -> tuple[str, str] | None:
        matches = tuple(
            bundle
            for bundle in skills_repository.list_bundles(run_id=source.run_id)
            if bundle.task_id == source.task_id
            and bundle.agent_id == source.agent_id
            and bundle.agent_revision == source.agent_revision
            and (source.step_id is None or bundle.step_id in {None, source.step_id})
        )
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "multiple canonical Skill Bundles match one Agent execution",
            )
        if not matches:
            return None
        bundle = matches[0]
        return bundle.skill_bundle_id, bundle.digest

    def capability_classification(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
    ) -> DataClassification:
        del capability
        return _bound_capability_classification(
            request,
            agents=base.agents.repository,
            bundles=bundles,
            run_bindings=run_bindings,
        )

    external_effect_recovery: ExternalEffectRecoveryCoordinator | None = None
    startup_recovery_extensions: tuple[StartupRecoveryExtension, ...] = ()
    if egress is not None:
        external_effect_recovery = ExternalEffectRecoveryCoordinator(
            SQLiteExternalEffectRecoveryRepository(
                database_dir / "external-effect-recovery.sqlite3"
            ),
            event_observer=ObservabilityExternalEffectRecoveryObserver(base.telemetry),
        )
        for descriptor in base.capabilities.inventory_providers():
            provider = base.capabilities.runtime_provider(descriptor.provider_id)
            if isinstance(provider, ExternalEffectReconciler):
                external_effect_recovery.register_reconciler(
                    descriptor.provider_id,
                    provider,
                )
        register_external_effect_recovery_control_plane(
            base.control_plane,
            external_effect_recovery,
        )
        startup_recovery_extensions = (
            ExternalEffectStartupRecovery(external_effect_recovery),
        )

    trace_hierarchy = TraceHierarchy(base.telemetry)
    capability_turn = (
        None
        if egress is None
        else AgentCapabilityTurn(
            base.model_runtime,
            base.capabilities,
            egress.capability_invoker(
                base.capabilities,
                canonical_binding_hook=bind_canonical_capability_invocation,
                classification_resolver=capability_classification,
                observer=ObservabilityInvocationObserver(base.telemetry),
                external_effect_recovery=external_effect_recovery,
            ),
        )
    )

    lifecycle = CanonicalContextAgentLifecycleBackend(
        delegate=base.pre_authorization_lifecycle,
        tasks=tasks,
        agents=base.agent_runtime,
        models=base.model_runtime,
        assembly=assembly,
        context_runtime=context_runtime,
        binding_factory=binding_factory,
        skill_bundle_resolver=skill_bundle_resolver,
        capability_turn=capability_turn,
        trace_hierarchy=trace_hierarchy,
    )
    authorized_lifecycle = AuthorizedLifecycleBackend(
        lifecycle,
        base.approval_gate,
        allow_internal_service_reads=True,
    )
    base.lifecycle_binding.bind_final(
        _TaskProjectScopeLifecycleBackend(authorized_lifecycle, tasks)
    )

    register_context_control_plane(
        base.control_plane,
        bundles,
        run_bindings,
        visibility=AuthorizationContextEntryVisibilityResolver(base.authorization),
    )
    register_skill_control_plane(base.control_plane, skills)

    reconciliation = reconcile_context_run_bindings(
        agents=base.agents.repository,
        bundles=bundles,
        bindings=run_bindings,
    )
    return SingleNodeContextComposition(
        bundles=bundles,
        run_bindings=run_bindings,
        assembly=assembly,
        runtime=context_runtime,
        lifecycle=lifecycle,
        memory=memory,
        knowledge=knowledge,
        skills_repository=skills_repository,
        skills=skills,
        research_repository=research_repository,
        research=research,
        reconciliation=reconciliation,
        external_effect_recovery=external_effect_recovery,
        startup_recovery_extensions=startup_recovery_extensions,
    )


__all__ = [
    "CONTEXT_OUTPUT_RESERVE_TOKENS",
    "SingleNodeContextComposition",
    "install_single_node_context",
]
