"""Single-node composition helpers for the operational #590 Context Bundle boundary.

This module keeps deployment wiring declarative: source domains retain their own durable stores,
#15 remains the authorization authority, and Context owns only assembly/binding evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.agents import AgentRuntime, AgentService
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import AuthorizationProvider, ContractError, ErrorCode, LifecycleBackend
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.data import (
    DataProviderSet,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)
from ai_multi_agent_platform.data.contracts import FileProvider, KnowledgeProvider, MemoryProvider
from ai_multi_agent_platform.kernel.repository import RunRepository, TaskRepository
from ai_multi_agent_platform.models import ModelRegistry, ModelRuntime
from ai_multi_agent_platform.repositories import RepositoryService
from ai_multi_agent_platform.repositories.service import RepositoryProvenanceStore
from ai_multi_agent_platform.research import (
    ResearchService,
    SqliteResearchRepository,
    register_research_control_plane,
)
from ai_multi_agent_platform.security import (
    AuthorizationGate,
    AuthorizedDataFileProvider,
    AuthorizedDataKnowledgeProvider,
    AuthorizedDataMemoryProvider,
)
from ai_multi_agent_platform.security.egress import EgressGate
from ai_multi_agent_platform.skills import (
    JsonSkillRepository,
    SkillExecutionCoordinator,
    SkillResolver,
    SkillService,
    register_skill_control_plane,
)

from .bindings import JsonContextRunBindingRepository
from .control_plane import register_context_control_plane
from .lifecycle import CanonicalContextAgentLifecycleBackend, ContextLifecycleSourceRequest
from .operational import (
    ContextRoutingPolicy,
    ModelRegistryContextEgressTargetResolver,
    OperationalContextBoundAgentRuntime,
)
from .persistence import JsonContextBundleRepository
from .reconciliation import ContextBindingReconciliationReport, reconcile_context_run_bindings
from .resolver import ContextAssemblyService, ContextResolver
from .source_adapters import (
    AgentContextSourceAdapter,
    ContextSourceAdapterBinding,
    FileArtifactResultContextSourceAdapter,
    KnowledgeContextSourceAdapter,
    MemoryContextSourceAdapter,
    OperationalContextAssemblyService,
    PlanStepContextSourceAdapter,
    RepositoryContextSourceAdapter,
    ResearchEvidenceContextSourceAdapter,
    SkillBundleContextSourceAdapter,
    TaskContextSourceAdapter,
)
from .models import ContextEntryRole, ContextSourceType
from .visibility import AuthorizationContextEntryVisibilityResolver

SINGLE_NODE_CONTEXT_OUTPUT_RESERVE_TOKENS = 2_048


@dataclass(slots=True)
class SingleNodeContextComposition:
    """Long-lived Context/data/Skill/Research components added to the single-node runtime."""

    bundles: JsonContextBundleRepository
    run_bindings: JsonContextRunBindingRepository
    assembly: OperationalContextAssemblyService
    runtime: OperationalContextBoundAgentRuntime
    lifecycle: CanonicalContextAgentLifecycleBackend
    data_providers: DataProviderSet
    memory: MemoryProvider
    knowledge: KnowledgeProvider
    skills_repository: JsonSkillRepository
    skills: SkillService
    skill_execution: SkillExecutionCoordinator
    research_repository: SqliteResearchRepository
    research: ResearchService
    reconciliation: ContextBindingReconciliationReport

    def register_control_plane(
        self,
        control_plane,
        *,
        authorization: AuthorizationProvider,
        agents: AgentService,
    ) -> None:
        """Register one shared northbound inspection surface for Context, Skills and Research."""

        register_context_control_plane(
            control_plane,
            self.bundles,
            self.run_bindings,
            visibility=AuthorizationContextEntryVisibilityResolver(authorization),
        )
        register_skill_control_plane(
            control_plane,
            self.skills,
            coordinator=self.skill_execution,
            agents=agents,
        )
        register_research_control_plane(control_plane, self.research)


def build_single_node_context_composition(
    *,
    database_dir: Path,
    delegate: LifecycleBackend,
    tasks: TaskRepository,
    runs: RunRepository,
    agents: AgentService,
    agent_runtime: AgentRuntime,
    models: ModelRegistry,
    model_runtime: ModelRuntime,
    capabilities: CapabilityRegistry,
    authorization: AuthorizationProvider,
    approval_gate: AuthorizationGate,
    files: FileProvider,
    coordination: CoordinatorRepository,
    repository_provenance: RepositoryProvenanceStore,
    repositories: RepositoryService,
) -> SingleNodeContextComposition:
    """Build the no-paid-service reference composition used by normal single-node execution."""

    bundles = JsonContextBundleRepository(database_dir / "context-bundles.json")
    run_bindings = JsonContextRunBindingRepository(database_dir / "context-run-bindings.json")

    protected_files = AuthorizedDataFileProvider(files, approval_gate)
    memory = AuthorizedDataMemoryProvider(
        LocalMemoryProvider(database_dir / "memory.sqlite3"),
        approval_gate,
    )
    knowledge = AuthorizedDataKnowledgeProvider(
        LocalKnowledgeProvider(database_dir / "knowledge.sqlite3"),
        approval_gate,
    )
    data_providers = DataProviderSet(
        files=protected_files,
        memory=memory,
        knowledge=knowledge,
    )

    skills_repository = JsonSkillRepository(database_dir / "skills.json")
    skills = SkillService(skills_repository)
    skill_execution = SkillExecutionCoordinator(
        SkillResolver(skills_repository, capability_registry=capabilities)
    )
    research_repository = SqliteResearchRepository(database_dir / "research.sqlite3")
    research = ResearchService(research_repository, authorization=approval_gate)

    canonical_assembly = ContextAssemblyService(
        ContextResolver(authorization),
        bundles,
    )
    assembly = OperationalContextAssemblyService(canonical_assembly)
    runtime = OperationalContextBoundAgentRuntime(
        agent_runtime,
        bundle_repository=bundles,
        binding_repository=run_bindings,
        egress_gate=EgressGate(),
        target_resolver=ModelRegistryContextEgressTargetResolver(models),
        routing_policy=ContextRoutingPolicy(
            output_reserve_tokens=SINGLE_NODE_CONTEXT_OUTPUT_RESERVE_TOKENS
        ),
    )

    task_adapter = TaskContextSourceAdapter(tasks)
    agent_adapter = AgentContextSourceAdapter(agents)
    plan_step_adapter = PlanStepContextSourceAdapter(coordination, runs=runs)
    skill_adapter = SkillBundleContextSourceAdapter(skills_repository)
    research_adapter = ResearchEvidenceContextSourceAdapter(research)
    repository_adapter = RepositoryContextSourceAdapter(
        repository_provenance,
        repositories=repositories,
        tasks=tasks,
    )
    file_adapter = FileArtifactResultContextSourceAdapter(protected_files, tasks, runs)
    memory_adapter = MemoryContextSourceAdapter(memory)
    knowledge_adapter = KnowledgeContextSourceAdapter(knowledge, agents, tasks=tasks)

    def source_bindings(
        request: ContextLifecycleSourceRequest,
    ) -> tuple[ContextSourceAdapterBinding, ...]:
        bindings: list[ContextSourceAdapterBinding] = [
            ContextSourceAdapterBinding(
                task_adapter,
                ContextSourceType.TASK,
                request.task_id,
                ContextEntryRole.CONTEXT,
                mandatory=True,
                project_id=request.project_id,
                workspace_id=request.workspace_id,
            ),
            ContextSourceAdapterBinding(
                agent_adapter,
                ContextSourceType.AGENT,
                request.agent_id,
                ContextEntryRole.INSTRUCTION,
                mandatory=True,
                project_id=request.project_id,
                workspace_id=request.workspace_id,
            ),
        ]
        if request.plan_id is not None:
            bindings.append(
                ContextSourceAdapterBinding(
                    plan_step_adapter,
                    ContextSourceType.PLAN_STEP,
                    request.step_id or request.plan_id,
                    ContextEntryRole.CONTEXT,
                    mandatory=request.step_id is not None,
                    record_absence=True,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                )
            )
        skill_ref = _skill_bundle_ref(skills_repository, request)
        if skill_ref is not None:
            bindings.append(
                ContextSourceAdapterBinding(
                    skill_adapter,
                    ContextSourceType.SKILL,
                    skill_ref[0],
                    ContextEntryRole.INSTRUCTION,
                    mandatory=True,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                )
            )
        bindings.extend(
            (
                ContextSourceAdapterBinding(
                    research_adapter,
                    ContextSourceType.RESEARCH_EVIDENCE,
                    f"research:{request.run_id}",
                    ContextEntryRole.EVIDENCE,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    repository_adapter,
                    ContextSourceType.REPOSITORY,
                    f"repository:{request.run_id}",
                    ContextEntryRole.EVIDENCE,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    file_adapter,
                    ContextSourceType.FILE,
                    f"files:{request.run_id}",
                    ContextEntryRole.CONTEXT,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    memory_adapter,
                    ContextSourceType.MEMORY,
                    f"memory:{request.task_id}:{request.agent_id}",
                    ContextEntryRole.CONTEXT,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                ),
                ContextSourceAdapterBinding(
                    knowledge_adapter,
                    ContextSourceType.KNOWLEDGE,
                    f"knowledge:{request.agent_id}",
                    ContextEntryRole.CONTEXT,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                ),
            )
        )
        return tuple(bindings)

    def skill_bundle_ref(
        request: ContextLifecycleSourceRequest,
    ) -> tuple[str, str] | None:
        return _skill_bundle_ref(skills_repository, request)

    lifecycle = CanonicalContextAgentLifecycleBackend(
        delegate=delegate,
        tasks=tasks,
        agents=agent_runtime,
        models=model_runtime,
        assembly=assembly,
        context_runtime=runtime,
        binding_factory=source_bindings,
        skill_bundle_resolver=skill_bundle_ref,
    )

    reconciliation = reconcile_context_run_bindings(
        agents=agents.repository,
        bundles=bundles,
        bindings=run_bindings,
    )
    return SingleNodeContextComposition(
        bundles=bundles,
        run_bindings=run_bindings,
        assembly=assembly,
        runtime=runtime,
        lifecycle=lifecycle,
        data_providers=data_providers,
        memory=memory,
        knowledge=knowledge,
        skills_repository=skills_repository,
        skills=skills,
        skill_execution=skill_execution,
        research_repository=research_repository,
        research=research,
        reconciliation=reconciliation,
    )


def _skill_bundle_ref(
    repository: JsonSkillRepository,
    request: ContextLifecycleSourceRequest,
) -> tuple[str, str] | None:
    matches = tuple(
        bundle
        for bundle in repository.list_bundles(run_id=request.run_id)
        if bundle.task_id == request.task_id
        and bundle.agent_id == request.agent_id
        and bundle.agent_revision == request.agent_revision
        and bundle.step_id == request.step_id
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise ContractError(
            ErrorCode.CONFLICT,
            "multiple canonical Skill Bundles match one Agent execution",
            details={"run_id": request.run_id, "agent_id": request.agent_id},
        )
    bundle = matches[0]
    return bundle.skill_bundle_id, bundle.digest


__all__ = [
    "SINGLE_NODE_CONTEXT_OUTPUT_RESERVE_TOKENS",
    "SingleNodeContextComposition",
    "build_single_node_context_composition",
]
