"""Operational #590 Context Bundle composition for the public single-node runtime.

The lower-level deployment builder intentionally stays reusable for focused embeddings. The normal
server-facing composition installs this module after its durable domain repositories are available
so every canonical Agent-bound Run crosses the Context Bundle boundary before model execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ai_multi_agent_platform.agents import AgentCapabilityTurn
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilitySpec,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.context.bindings import JsonContextRunBindingRepository
from ai_multi_agent_platform.context.classification import effective_context_bundle_classification
from ai_multi_agent_platform.context.control_plane import register_context_control_plane
from ai_multi_agent_platform.context.lifecycle import (
    CanonicalContextAgentLifecycleBackend,
    ContextLifecycleSourceRequest,
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
from ai_multi_agent_platform.context.resolver import ContextAssemblyService, ContextResolver
from ai_multi_agent_platform.context.source_adapters import (
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
from ai_multi_agent_platform.context.visibility import AuthorizationContextEntryVisibilityResolver
from ai_multi_agent_platform.contracts import ContractError, DataClassification, ErrorCode
from ai_multi_agent_platform.data import LocalKnowledgeProvider, LocalMemoryProvider
from ai_multi_agent_platform.kernel import EventSourcedRunRepository, EventSourcedTaskRepository
from ai_multi_agent_platform.research import (
    ResearchService,
    SqliteResearchRepository,
    register_research_control_plane,
)
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

from .egress_bindings import EgressDeploymentBindings

if TYPE_CHECKING:
    from ai_multi_agent_platform.deployment.single_node import (
        SingleNodeDeployment as BaseDeployment,
    )


# Explicit provider-neutral output reserve. This is intentionally a platform constant rather than
# an adapter/model-specific guess and can later become deployment configuration without changing
# Context Bundle identity semantics.
CONTEXT_OUTPUT_RESERVE_TOKENS = 2_048


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


def install_single_node_context(
    base: BaseDeployment,
    *,
    egress: EgressDeploymentBindings | None = None,
) -> SingleNodeContextComposition:
    """Install the canonical Context Bundle path into one already-built single-node deployment.

    The installer replaces only the kernel lifecycle participant. Every other canonical owner
    remains unchanged and one #15 authorization wrapper stays the outer execution boundary. This
    keeps Task/Run/Agent ownership intact while making #590 the effective context authority.

    When the public deployment supplies #591 bindings, Context rendering and capability execution
    share that exact durable egress gate. Focused lower-level embeddings may omit the bindings and
    retain the conservative local default gate.
    """

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
        routing_policy=ContextRoutingPolicy(
            output_reserve_tokens=CONTEXT_OUTPUT_RESERVE_TOKENS,
        ),
    )

    # Reuse the authoritative local File store. Memory/Knowledge use canonical local SQLite
    # providers so the reference profile remains fully local and requires no hosted RAG/vector
    # service. Authorization wrappers preserve #15 at each source boundary.
    protected_files = AuthorizedDataFileProvider(base.files, base.approval_gate)
    memory = LocalMemoryProvider(database_dir / "memory.sqlite3")
    knowledge = LocalKnowledgeProvider(database_dir / "knowledge.sqlite3")
    protected_memory = AuthorizedDataMemoryProvider(memory, base.approval_gate)
    protected_knowledge = AuthorizedDataKnowledgeProvider(knowledge, base.approval_gate)

    skills_repository = JsonSkillRepository(database_dir / "skills.json")
    skills = SkillService(skills_repository)
    research_repository = SqliteResearchRepository(database_dir / "research.sqlite3")
    research = ResearchService(research_repository, authorization=base.approval_gate)

    task_adapter = TaskContextSourceAdapter(tasks)
    agent_adapter = AgentContextSourceAdapter(base.agents)
    plan_adapter = PlanStepContextSourceAdapter(base.coordination_repository, runs=runs)
    skill_adapter = SkillBundleContextSourceAdapter(skills_repository)
    research_adapter = ResearchEvidenceContextSourceAdapter(research)
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
        matches = tuple(
            bundle
            for bundle in bundles.list_for_run(request.trace.run_id)
            if bundle.task_id == request.trace.task_id
            and bundle.agent_id == request.trace.agent_id
        )
        if len(matches) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Context capability egress requires exactly one canonical Context Bundle",
                details={
                    "run_id": request.trace.run_id,
                    "agent_id": request.trace.agent_id,
                    "matching_context_bundles": len(matches),
                },
            )
        return effective_context_bundle_classification(matches[0])

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
            ),
        )
    )

    previous_lifecycle = base.kernel._lifecycle  # noqa: SLF001 - composition boundary replacement
    # The base profile intentionally already wraps its inner lifecycle with #15. Reuse that inner
    # participant as the fallback and make one fresh #15 wrapper the outermost boundary, avoiding
    # duplicate authorization/audit events for non-Agent executions.
    fallback = getattr(previous_lifecycle, "_inner", previous_lifecycle)
    lifecycle = CanonicalContextAgentLifecycleBackend(
        delegate=fallback,
        tasks=tasks,
        agents=base.agent_runtime,
        models=base.model_runtime,
        assembly=assembly,
        context_runtime=context_runtime,
        binding_factory=binding_factory,
        skill_bundle_resolver=skill_bundle_resolver,
        capability_turn=capability_turn,
    )
    base.kernel._lifecycle = AuthorizedLifecycleBackend(  # noqa: SLF001
        lifecycle,
        base.approval_gate,
        allow_internal_service_reads=True,
    )

    register_context_control_plane(
        base.control_plane,
        bundles,
        run_bindings,
        visibility=AuthorizationContextEntryVisibilityResolver(base.authorization),
    )
    # These domains are canonical source owners for Context and therefore join the ordinary Control
    # Plane instead of becoming Context-private stores.
    register_skill_control_plane(base.control_plane, skills)
    register_research_control_plane(base.control_plane, research)

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
    )


__all__ = [
    "CONTEXT_OUTPUT_RESERVE_TOKENS",
    "SingleNodeContextComposition",
    "install_single_node_context",
]
