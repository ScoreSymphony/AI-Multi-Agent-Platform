"""Operational source adapters for canonical Context Bundle assembly.

Source domains remain authoritative. These adapters only project exact canonical state into
Context candidates and never own Task, Agent, Skill, Research, Repository or data lifecycles.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.data.contracts import KnowledgeProvider, MemoryProvider
from ai_multi_agent_platform.data.models import (
    DataAccessContext,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    MemoryQuery,
    MemoryScope,
)
from ai_multi_agent_platform.kernel.repository import RunRepository, TaskRepository
from ai_multi_agent_platform.research import EvidenceFreshness, ResearchService
from ai_multi_agent_platform.skills import ReferenceSkillRenderer, SkillBundle, SkillRepository

from .file_source_adapter import FileArtifactResultContextSourceAdapter
from .models import (
    ContextBundle,
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextFreshness,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
)
from .repository_source_adapter import RepositoryContextSourceAdapter
from .resolver import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextSourceAdapter,
    ContextSourceRequest,
)
from .source_adapter_normalization import (
    canonical_json as _canonical_json,
    content_digest as _digest,
    normalize_classification as _classification,
    operational_request as _operational_request,
)

_UNAVAILABLE_CODES = frozenset(
    {
        ErrorCode.NOT_FOUND,
        ErrorCode.UNAVAILABLE,
        ErrorCode.BACKEND_ERROR,
        ErrorCode.TIMEOUT,
        ErrorCode.TRANSIENT_FAILURE,
    }
)


@dataclass(frozen=True, slots=True)
class OperationalContextSourceRequest:
    """Strict superset of the Context source request used only at operational composition."""

    task_id: str
    run_id: str
    agent_id: str
    agent_revision: int
    project_id: str | None
    workspace_id: str | None
    operation: OperationContext
    actor_ref: str
    plan_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContextSourceAdapterBinding:
    """Expected source identity used to make provider failure explicit and reproducible."""

    adapter: ContextSourceAdapter
    source_type: ContextSourceType
    source_id: str
    role: ContextEntryRole
    mandatory: bool = False
    record_absence: bool = False
    project_id: str | None = None
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        if not self.adapter.adapter_id.strip():
            raise ValueError("Context source adapter ID must not be blank")
        if not self.source_id.strip():
            raise ValueError("Context expected source ID must not be blank")
        if self.mandatory and not self.record_absence:
            object.__setattr__(self, "record_absence", True)


class OperationalContextAssemblyService:
    """Normalize source availability, then delegate truth to canonical Context assembly."""

    def __init__(self, canonical: ContextAssemblyService) -> None:
        self.canonical = canonical

    async def assemble(
        self,
        request: ContextAssemblyRequest,
        *,
        bindings: Sequence[ContextSourceAdapterBinding],
    ) -> ContextBundle:
        source_request = OperationalContextSourceRequest(
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
            agent_revision=request.agent_revision,
            project_id=request.operation.project_id,
            workspace_id=request.workspace_id,
            operation=request.operation,
            actor_ref=request.actor.actor_id,
            plan_id=request.plan_id,
            step_id=request.step_id,
        )
        candidates = list(request.candidates)
        seen: set[str] = set()
        for binding in sorted(bindings, key=lambda item: item.adapter.adapter_id):
            adapter_id = binding.adapter.adapter_id
            if adapter_id in seen:
                raise ValueError(f"duplicate context source adapter ID: {adapter_id}")
            seen.add(adapter_id)
            try:
                collected = await binding.adapter.collect(
                    cast(ContextSourceRequest, source_request)
                )
            except ContractError as exc:
                if exc.code not in _UNAVAILABLE_CODES:
                    raise
                candidates.append(
                    _unavailable_candidate(
                        binding,
                        availability=(
                            "missing" if exc.code is ErrorCode.NOT_FOUND else "unavailable"
                        ),
                        detail=exc.message,
                    )
                )
                continue
            if not collected and binding.record_absence:
                candidates.append(
                    _unavailable_candidate(
                        binding,
                        availability="missing",
                        detail="expected canonical context source produced no contribution",
                    )
                )
                continue
            candidates.extend(collected)
        return await self.canonical.assemble(
            replace(request, candidates=tuple(candidates)),
            adapters=(),
        )


def _unavailable_candidate(
    binding: ContextSourceAdapterBinding,
    *,
    availability: str,
    detail: str,
) -> ContextCandidate:
    marker = (
        f"context-source:{binding.adapter.adapter_id}:{binding.source_type.value}:"
        f"{binding.source_id}:{availability}"
    )
    digest = _digest(marker)
    return ContextCandidate(
        source=ContextSourceRef(
            binding.source_type,
            binding.source_id,
            digest=digest,
            locator=f"adapter:{binding.adapter.adapter_id}:{availability}",
        ),
        role=binding.role,
        selection_reason=f"{availability} source: {detail}",
        mandatory=binding.mandatory,
        content_ref=f"context-source-unavailable:{digest}",
        content_digest=digest,
        freshness=ContextFreshness.UNAVAILABLE,
        trust=ContextTrust.UNKNOWN,
        data_classification=ContextDataClassification.INTERNAL,
        project_id=binding.project_id,
        workspace_id=binding.workspace_id,
        metadata={
            "adapter_id": binding.adapter.adapter_id,
            "availability": availability,
        },
    )


class TaskContextSourceAdapter:
    adapter_id = "platform.task-context/v1"

    def __init__(self, tasks: TaskRepository) -> None:
        self.tasks = tasks

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        state = await self.tasks.get_task(request.task_id)
        task = state.task
        content = _canonical_json(
            {
                "task_id": task.id,
                "title": task.title,
                "objective": task.description,
                "status": task.status.value,
                "project_id": task.project_id,
                "revision": state.revision,
            }
        )
        digest = _digest(content)
        return (
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.TASK,
                    task.id,
                    revision=str(state.revision),
                    digest=digest,
                ),
                role=ContextEntryRole.CONTEXT,
                selection_reason="canonical Task intent and constraints",
                mandatory=True,
                inline_content=content,
                content_digest=digest,
                trust=ContextTrust.TRUSTED,
                data_classification=ContextDataClassification.INTERNAL,
                priority=100,
                relevance=1.0,
                project_id=task.project_id,
                conflict_key=f"task:{task.id}",
            ),
        )


class AgentContextSourceAdapter:
    adapter_id = "platform.agent-context/v1"

    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        revision = self.agents.get_agent_revision(request.agent_id, request.agent_revision)
        role_source = revision.profile.instructions.role
        if role_source.content is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Agent instruction reference has no resolved content at the consuming boundary",
            )
        content = _canonical_json(
            {
                "agent_id": revision.agent_id,
                "agent_revision": revision.revision,
                "name": revision.profile.name,
                "role": revision.profile.role,
                "instructions": role_source.content,
                "instruction_version": role_source.version,
                "platform_constraint_refs": list(
                    revision.profile.instructions.platform_constraint_refs
                ),
                "project_instruction_refs": list(
                    revision.profile.instructions.project_instruction_refs
                ),
            }
        )
        digest = _digest(content)
        return (
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.AGENT,
                    revision.agent_id,
                    revision=str(revision.revision),
                    digest=digest,
                ),
                role=ContextEntryRole.INSTRUCTION,
                selection_reason="exact canonical Agent revision and instructions",
                mandatory=True,
                inline_content=content,
                content_digest=digest,
                trust=ContextTrust.TRUSTED,
                data_classification=ContextDataClassification.INTERNAL,
                priority=100,
                relevance=1.0,
                project_id=revision.project_id,
                workspace_id=revision.workspace_id,
                conflict_key=f"agent:{revision.agent_id}",
            ),
        )


class PlanStepContextSourceAdapter:
    adapter_id = "platform.plan-step-context/v1"

    def __init__(
        self,
        coordinator: CoordinatorRepository,
        *,
        runs: RunRepository | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.runs = runs

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.plan_id is None or request.step_id is None:
            return ()
        state = self.coordinator.get_plan(request.plan_id)
        record = self.coordinator.get_step_record(request.step_id)
        step = next((item for item in state.steps if item.id == request.step_id), None)
        if step is None:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Step is not in requested Plan")
        content = _canonical_json(
            {
                "plan_id": state.plan.id,
                "plan_revision": state.plan.revision,
                "plan_store_revision": state.store_revision,
                "step_id": step.id,
                "step_title": step.title,
                "step_status": step.status.value,
                "dependencies": list(step.depends_on),
                "coordination_revision": record.revision,
                "coordination_phase": record.phase.value,
            }
        )
        digest = _digest(content)
        candidates: list[ContextCandidate] = [
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.PLAN_STEP,
                    step.id,
                    revision=(
                        f"plan:{state.plan.revision};store:{state.store_revision};"
                        f"step:{record.revision}"
                    ),
                    digest=digest,
                ),
                role=ContextEntryRole.CONTEXT,
                selection_reason="current canonical Plan/Step purpose and dependency state",
                inline_content=content,
                content_digest=digest,
                trust=ContextTrust.TRUSTED,
                data_classification=ContextDataClassification.INTERNAL,
                priority=80,
                relevance=1.0,
                project_id=step.project_id,
                conflict_key=f"step:{step.id}",
            )
        ]
        if self.runs is None:
            return tuple(candidates)
        for dependency_id in sorted(step.depends_on):
            dependency = self.coordinator.get_step_record(dependency_id)
            if dependency.latest_run_id is None:
                continue
            prior = await self.runs.get_run(request.task_id, dependency.latest_run_id)
            prior_content = _canonical_json(
                {
                    "run_id": prior.run_id,
                    "step_id": dependency_id,
                    "status": prior.status.value,
                    "output": prior.output,
                    "result_ids": list(prior.result_ids),
                    "artifact_ids": list(prior.artifact_ids),
                    "revision": prior.revision,
                }
            )
            prior_digest = _digest(prior_content)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.PRIOR_RUN,
                        prior.run_id,
                        revision=str(prior.revision),
                        digest=prior_digest,
                    ),
                    role=ContextEntryRole.EVIDENCE,
                    selection_reason="completed predecessor Run output for current Step",
                    inline_content=prior_content,
                    content_digest=prior_digest,
                    trust=ContextTrust.TRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=70,
                    relevance=0.9,
                    project_id=request.project_id,
                )
            )
        return tuple(candidates)


class SkillBundleContextSourceAdapter:
    adapter_id = "platform.skill-bundle-context/v1"

    def __init__(self, repository: SkillRepository) -> None:
        self.repository = repository
        self.renderer = ReferenceSkillRenderer()

    def bundle_for(self, request: ContextSourceRequest) -> SkillBundle | None:
        matches = tuple(
            bundle
            for bundle in self.repository.list_bundles(run_id=request.run_id)
            if bundle.task_id == request.task_id
            and bundle.agent_id == request.agent_id
            and bundle.agent_revision == request.agent_revision
            and (request.step_id is None or bundle.step_id in {None, request.step_id})
        )
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "multiple effective Skill Bundles match one Context assembly request",
            )
        return matches[0] if matches else None

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        bundle = self.bundle_for(request)
        if bundle is None:
            return ()
        rendered = self.renderer.render(bundle, self.repository)
        content = rendered.content or _canonical_json(
            {
                "skill_bundle_id": bundle.skill_bundle_id,
                "skill_count": 0,
            }
        )
        digest = _digest(content)
        return (
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.SKILL,
                    bundle.skill_bundle_id,
                    revision=bundle.resolver_version,
                    digest=bundle.digest,
                ),
                role=ContextEntryRole.INSTRUCTION,
                selection_reason="exact resolved Skill Bundle for this execution",
                mandatory=True,
                inline_content=content,
                content_digest=digest,
                trust=ContextTrust.TRUSTED,
                data_classification=ContextDataClassification.INTERNAL,
                priority=90,
                relevance=1.0,
                project_id=bundle.project_id,
                workspace_id=bundle.workspace_id,
                conflict_key=f"skill-bundle:{bundle.skill_bundle_id}",
                metadata={
                    "skill_bundle_digest": bundle.digest,
                    "resolver_version": bundle.resolver_version,
                    "policy_version": bundle.policy_version,
                },
            ),
        )


class ResearchEvidenceContextSourceAdapter:
    adapter_id = "platform.research-evidence-context/v1"

    def __init__(self, research: ResearchService) -> None:
        self.research = research

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = []
        for item in self.research.repository.list_items():
            if item.run_id != request.run_id and item.task_id != request.task_id:
                continue
            for evidence in self.research.repository.list_evidence(item.research_item_id):
                claim = self.research.repository.get_claim(evidence.claim_id)
                freshness = self.research.evidence_freshness(evidence.evidence_id)
                normalized = {
                    EvidenceFreshness.CURRENT: ContextFreshness.CURRENT,
                    EvidenceFreshness.STALE: ContextFreshness.STALE,
                    EvidenceFreshness.UNAVAILABLE: ContextFreshness.UNAVAILABLE,
                    EvidenceFreshness.UNVERIFIABLE: ContextFreshness.UNKNOWN,
                }[freshness]
                content = _canonical_json(
                    {
                        "research_item_id": item.research_item_id,
                        "research_item_revision": item.revision,
                        "claim_id": claim.claim_id,
                        "claim_revision": claim.revision,
                        "claim": claim.text,
                        "claim_status": claim.status.value,
                        "evidence_id": evidence.evidence_id,
                        "relation": evidence.relation.value,
                        "location_ref": evidence.location_ref,
                        "source_revision": evidence.source_revision,
                        "source_version": evidence.source_version,
                        "source_commit": evidence.source_commit,
                        "source_etag": evidence.source_etag,
                        "source_snapshot_digest": evidence.source_snapshot_digest,
                        "artifact_id": evidence.artifact_id,
                    }
                )
                digest = _digest(content)
                candidates.append(
                    ContextCandidate(
                        source=ContextSourceRef(
                            ContextSourceType.RESEARCH_EVIDENCE,
                            evidence.evidence_id,
                            revision=(
                                evidence.source_commit
                                or evidence.source_revision
                                or evidence.source_version
                                or str(claim.revision)
                            ),
                            digest=evidence.digest,
                            snapshot_id=evidence.artifact_id,
                            locator=evidence.location_ref,
                        ),
                        role=ContextEntryRole.EVIDENCE,
                        selection_reason="canonical Research Claim/Evidence linked to Task or Run",
                        inline_content=content,
                        content_digest=digest,
                        freshness=normalized,
                        trust=ContextTrust.UNTRUSTED,
                        data_classification=_classification(item.data_class),
                        priority=40,
                        relevance=0.8,
                        project_id=item.project_id,
                        workspace_id=item.workspace_id,
                        conflict_key=f"research-claim:{claim.claim_id}",
                    )
                )
        return tuple(candidates)


class MemoryContextSourceAdapter:
    adapter_id = "platform.memory-context/v1"

    def __init__(self, memory: MemoryProvider) -> None:
        self.memory = memory

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        operation, actor_ref = _operational_request(request)
        access = DataAccessContext(
            operation=operation,
            actor_ref=actor_ref,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
        )
        scopes: list[MemoryQuery] = [
            MemoryQuery(MemoryScope.TASK, request.task_id),
            MemoryQuery(MemoryScope.AGENT, request.agent_id),
        ]
        if request.project_id is not None:
            scopes.append(MemoryQuery(MemoryScope.WORKSPACE, request.project_id))
        candidates: list[ContextCandidate] = []
        for query in scopes:
            for entry in await self.memory.query_entries(query, access):
                classification = _classification(entry.classification)
                if classification is ContextDataClassification.SECRET_REFERENCE:
                    continue
                body = _canonical_json(entry.value)
                digest = _digest(body)
                candidates.append(
                    ContextCandidate(
                        source=ContextSourceRef(
                            ContextSourceType.MEMORY,
                            entry.memory_id,
                            digest=digest,
                        ),
                        role=ContextEntryRole.CONTEXT,
                        selection_reason=f"canonical scoped Memory entry ({entry.scope.value})",
                        inline_content=body,
                        content_digest=digest,
                        freshness=(
                            ContextFreshness.STALE if entry.expired else ContextFreshness.CURRENT
                        ),
                        trust=(
                            ContextTrust.TRUSTED
                            if entry.created_by == actor_ref
                            else ContextTrust.UNKNOWN
                        ),
                        data_classification=classification,
                        priority=20,
                        relevance=0.5,
                        project_id=request.project_id,
                        workspace_id=request.workspace_id,
                    )
                )
        return tuple(candidates)


class KnowledgeContextSourceAdapter:
    adapter_id = "platform.knowledge-context/v1"

    def __init__(self, knowledge: KnowledgeProvider, agents: AgentService) -> None:
        self.knowledge = knowledge
        self.agents = agents

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        revision = self.agents.get_agent_revision(request.agent_id, request.agent_revision)
        source_ids = revision.profile.data_access.knowledge_source_ids
        if not source_ids:
            return ()
        operation, actor_ref = _operational_request(request)
        access = DataAccessContext(
            operation=operation,
            actor_ref=actor_ref,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
        )
        task_query = await _task_search_text(request)
        results = await self.knowledge.search(
            KnowledgeSearchRequest(
                query=task_query,
                context=access,
                source_ids=source_ids,
                mode=KnowledgeSearchMode.KEYWORD,
                limit=20,
            )
        )
        candidates: list[ContextCandidate] = []
        for result in results:
            classification = _classification(result.classification)
            if classification is ContextDataClassification.SECRET_REFERENCE:
                continue
            digest = _digest(result.content)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.KNOWLEDGE,
                        result.document_id,
                        revision=result.revision,
                        digest=digest,
                        locator=result.location,
                    ),
                    role=ContextEntryRole.CONTEXT,
                    selection_reason="Agent-authorized Knowledge retrieval for current Task",
                    inline_content=result.content,
                    content_digest=digest,
                    trust=ContextTrust.UNTRUSTED,
                    data_classification=classification,
                    priority=25,
                    relevance=(
                        max(0.0, min(1.0, result.score)) if result.score is not None else 0.5
                    ),
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                )
            )
        return tuple(candidates)


async def _task_search_text(request: ContextSourceRequest) -> str:
    # Knowledge retrieval requires a stable non-blank query, while the source adapter contract
    # deliberately carries only canonical execution identity. Task intent is already mandatory
    # Context, so this bounded query remains deterministic and provider-neutral.
    return f"task {request.task_id} agent {request.agent_id}"


__all__ = [
    "AgentContextSourceAdapter",
    "ContextSourceAdapterBinding",
    "FileArtifactResultContextSourceAdapter",
    "KnowledgeContextSourceAdapter",
    "MemoryContextSourceAdapter",
    "OperationalContextAssemblyService",
    "OperationalContextSourceRequest",
    "PlanStepContextSourceAdapter",
    "RepositoryContextSourceAdapter",
    "ResearchEvidenceContextSourceAdapter",
    "SkillBundleContextSourceAdapter",
    "TaskContextSourceAdapter",
]
