"""Operational source adapters that project existing canonical domains into #590 context.

The adapters in this module never own source lifecycle. They read canonical repositories/providers,
retain source identity/revision/digest evidence, and emit ``ContextCandidate`` values for the
platform-owned resolver. Provider failures can be wrapped with ``GuardedContextSourceAdapter`` so
unavailability becomes explicit canonical evidence instead of an adapter-private exception.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts import DataClassification
from ai_multi_agent_platform.data import (
    DataAccessContext,
    FileProvider,
    KnowledgeProvider,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    MemoryProvider,
    MemoryQuery,
    MemoryScope,
)
from ai_multi_agent_platform.kernel import TaskRepository
from ai_multi_agent_platform.research import ResearchRepository
from ai_multi_agent_platform.skills import SkillRepository

from .models import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextFreshness,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
)
from .resolver import ContextSourceAdapter, ContextSourceRequest


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _classification(value: DataClassification | str | None) -> ContextDataClassification:
    raw = value.value if isinstance(value, DataClassification) else value
    mapping = {
        None: ContextDataClassification.INTERNAL,
        "public": ContextDataClassification.PUBLIC,
        "internal": ContextDataClassification.INTERNAL,
        "confidential": ContextDataClassification.CONFIDENTIAL,
        "restricted": ContextDataClassification.RESTRICTED,
        "secret": ContextDataClassification.SECRET_REFERENCE,
        "secret_reference": ContextDataClassification.SECRET_REFERENCE,
    }
    return mapping.get(raw, ContextDataClassification.INTERNAL)


class TaskContextSourceAdapter:
    adapter_id = "platform.context.task/v1"

    def __init__(self, tasks: TaskRepository) -> None:
        self.tasks = tasks

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        state = await self.tasks.get_task(request.task_id)
        task = state.task
        payload = _json(
            {
                "task_id": task.id,
                "title": task.title,
                "objective": task.description,
                "status": task.status.value,
                "project_id": task.project_id,
                "revision": state.revision,
            }
        )
        return (
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.TASK,
                    task.id,
                    revision=str(state.revision),
                    digest=_sha(payload),
                ),
                role=ContextEntryRole.INSTRUCTION,
                selection_reason="canonical Task intent and constraints",
                mandatory=True,
                inline_content=payload,
                trust=ContextTrust.TRUSTED,
                priority=100,
                relevance=1.0,
                project_id=task.project_id,
            ),
        )


class AgentContextSourceAdapter:
    adapter_id = "platform.context.agent/v1"

    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        revision = self.agents.get_agent_revision(request.agent_id, request.agent_revision)
        role = revision.profile.instructions.role
        content = role.content
        if content is None:
            # Reference-only instructions remain exact evidence. The consuming adapter may
            # resolve them through a ContextContentProvider when that canonical owner supports it.
            assert role.ref is not None
            return (
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.AGENT,
                        revision.agent_id,
                        revision=str(revision.revision),
                        locator=role.ref,
                    ),
                    role=ContextEntryRole.INSTRUCTION,
                    selection_reason="exact canonical Agent revision instructions",
                    mandatory=True,
                    content_ref=role.ref,
                    content_digest=role.digest,
                    trust=ContextTrust.SYSTEM,
                    priority=95,
                    relevance=1.0,
                    project_id=revision.project_id,
                    workspace_id=revision.workspace_id,
                ),
            )
        return (
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.AGENT,
                    revision.agent_id,
                    revision=str(revision.revision),
                    digest=_sha(content),
                ),
                role=ContextEntryRole.INSTRUCTION,
                selection_reason="exact canonical Agent revision instructions",
                mandatory=True,
                inline_content=content,
                trust=ContextTrust.SYSTEM,
                priority=95,
                relevance=1.0,
                project_id=revision.project_id,
                workspace_id=revision.workspace_id,
            ),
        )


class PlanStepRepository(Protocol):
    def get_plan(self, plan_id: str): ...
    def get_step_record(self, step_id: str): ...


class PlanStepContextSourceAdapter:
    adapter_id = "platform.context.plan-step/v1"

    def __init__(self, repository: PlanStepRepository) -> None:
        self.repository = repository

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.plan_id is None:
            return ()
        state = self.repository.get_plan(request.plan_id)
        entries: list[ContextCandidate] = []
        plan_payload = _json(
            {
                "plan_id": state.plan.id,
                "revision": state.plan.revision,
                "task_id": state.plan.task_id,
                "store_revision": state.store_revision,
            }
        )
        entries.append(
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.PLAN_STEP,
                    state.plan.id,
                    revision=str(state.plan.revision),
                    digest=_sha(plan_payload),
                ),
                role=ContextEntryRole.CONTEXT,
                selection_reason="active canonical Plan for execution",
                inline_content=plan_payload,
                trust=ContextTrust.TRUSTED,
                priority=80,
                relevance=0.9,
                project_id=state.plan.project_id,
            )
        )
        if request.step_id is not None:
            step = next((item for item in state.steps if item.id == request.step_id), None)
            record = self.repository.get_step_record(request.step_id)
            if step is not None:
                step_payload = _json(
                    {
                        "step_id": step.id,
                        "title": step.title,
                        "description": step.description,
                        "status": step.status.value,
                        "dependencies": list(record.dependency_ids),
                        "satisfied_dependencies": list(record.satisfied_dependency_ids),
                        "coordination_revision": record.revision,
                    }
                )
                entries.append(
                    ContextCandidate(
                        source=ContextSourceRef(
                            ContextSourceType.PLAN_STEP,
                            step.id,
                            revision=f"plan-{record.plan_revision}/coord-{record.revision}",
                            digest=_sha(step_payload),
                        ),
                        role=ContextEntryRole.INSTRUCTION,
                        selection_reason="current canonical Step purpose and predecessor state",
                        mandatory=True,
                        inline_content=step_payload,
                        trust=ContextTrust.TRUSTED,
                        priority=90,
                        relevance=1.0,
                        project_id=state.plan.project_id,
                    )
                )
        return tuple(entries)


class SkillBundleContextSourceAdapter:
    adapter_id = "platform.context.skill-bundle/v1"

    def __init__(self, skills: SkillRepository) -> None:
        self.skills = skills

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        bundles = self.skills.list_bundles(run_id=request.run_id)
        if not bundles:
            return ()
        bundle = bundles[-1]
        if (
            bundle.task_id != request.task_id
            or bundle.agent_id != request.agent_id
            or bundle.agent_revision != request.agent_revision
        ):
            return ()
        candidates: list[ContextCandidate] = []
        for entry in bundle.entries:
            revision = self.skills.get_skill_revision(entry.ref.skill_id, entry.ref.revision)
            content = revision.profile.content
            source = ContextSourceRef(
                ContextSourceType.SKILL,
                entry.ref.skill_id,
                revision=str(entry.ref.revision),
                digest=entry.content_digest,
                locator=content.ref,
            )
            kwargs = (
                {"inline_content": content.content}
                if content.content is not None
                else {"content_ref": content.ref, "content_digest": entry.content_digest}
            )
            candidates.append(
                ContextCandidate(
                    source=source,
                    role=ContextEntryRole.INSTRUCTION,
                    selection_reason=f"Skill Bundle {bundle.skill_bundle_id} exact method revision",
                    mandatory=True,
                    trust=ContextTrust.TRUSTED,
                    priority=85,
                    relevance=1.0,
                    project_id=revision.project_id,
                    workspace_id=revision.workspace_id,
                    **kwargs,
                )
            )
        return tuple(candidates)


class ResearchEvidenceContextSourceAdapter:
    adapter_id = "platform.context.research-evidence/v1"

    def __init__(self, research: ResearchRepository) -> None:
        self.research = research

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = []
        for item in self.research.list_items():
            if item.task_id not in {None, request.task_id}:
                continue
            if item.run_id not in {None, request.run_id}:
                continue
            for evidence in self.research.list_evidence(item.research_item_id):
                if evidence.task_id not in {None, request.task_id}:
                    continue
                if evidence.run_id not in {None, request.run_id}:
                    continue
                claim = self.research.get_claim(evidence.claim_id)
                payload = _json(
                    {
                        "claim_id": claim.claim_id,
                        "claim_revision": claim.revision,
                        "claim": claim.text,
                        "claim_status": claim.status.value,
                        "confidence": claim.confidence.value,
                        "evidence_id": evidence.evidence_id,
                        "relation": evidence.relation.value,
                        "location_ref": evidence.location_ref,
                        "source_revision": evidence.source_revision,
                        "source_commit": evidence.source_commit,
                        "source_content_digest": evidence.source_content_digest,
                        "source_snapshot_digest": evidence.source_snapshot_digest,
                    }
                )
                candidates.append(
                    ContextCandidate(
                        source=ContextSourceRef(
                            ContextSourceType.RESEARCH_EVIDENCE,
                            evidence.evidence_id,
                            revision=evidence.source_revision or str(claim.revision),
                            digest=evidence.digest,
                            snapshot_id=evidence.source_snapshot_digest,
                            locator=evidence.location_ref,
                        ),
                        role=ContextEntryRole.EVIDENCE,
                        selection_reason="canonical Research Claim/Evidence relevant to this Task/Run",
                        inline_content=payload,
                        trust=ContextTrust.UNTRUSTED,
                        priority=40,
                        relevance=0.7,
                        project_id=item.project_id,
                        workspace_id=item.workspace_id,
                    )
                )
        return tuple(candidates)


@dataclass(frozen=True, slots=True)
class RepositoryContextSlice:
    repository_id: str
    requested_revision: str
    resolved_revision: str
    path: str
    start_line: int
    end_line: int
    content: str
    intelligence_provider_id: str
    project_id: str | None = None
    workspace_id: str | None = None

    @property
    def digest(self) -> str:
        return _sha(self.content)


RepositorySliceLoader = Callable[
    [ContextSourceRequest], Awaitable[tuple[RepositoryContextSlice, ...]]
]


class RepositoryIntelligenceContextSourceAdapter:
    adapter_id = "platform.context.repository-intelligence/v1"

    def __init__(self, loader: RepositorySliceLoader) -> None:
        self.loader = loader

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        slices = await self.loader(request)
        return tuple(
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.REPOSITORY,
                    item.repository_id,
                    revision=item.resolved_revision,
                    digest=item.digest,
                    locator=f"{item.path}:{item.start_line}-{item.end_line}",
                ),
                role=ContextEntryRole.EVIDENCE,
                selection_reason="exact Repository Intelligence source slice",
                inline_content=item.content,
                trust=ContextTrust.UNTRUSTED,
                priority=45,
                relevance=0.8,
                project_id=item.project_id,
                workspace_id=item.workspace_id,
                metadata={
                    "requested_revision": item.requested_revision,
                    "intelligence_provider_id": item.intelligence_provider_id,
                    "path": item.path,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                },
            )
            for item in slices
        )


class FileReferenceContextSourceAdapter:
    """Expose exact file/artifact references without pre-authorizing or duplicating file bytes."""

    adapter_id = "platform.context.file-references/v1"

    def __init__(
        self,
        files: FileProvider,
        access_context: Callable[[ContextSourceRequest], DataAccessContext],
    ) -> None:
        self.files = files
        self.access_context = access_context

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        refs = set(request.resource_refs)
        if not refs:
            return ()
        context = self.access_context(request)
        records = await self.files.list_files(context)
        candidates: list[ContextCandidate] = []
        for record in records:
            file_selected = record.file_id in refs or f"file:{record.file_id}" in refs
            selected_artifacts = tuple(
                item
                for item in record.artifact_ids
                if item in refs or f"artifact:{item}" in refs
            )
            if not file_selected and not selected_artifacts:
                continue
            payload = _json(
                {
                    "file_id": record.file_id,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "content_type": record.content_type,
                    "artifact_ids": list(selected_artifacts or record.artifact_ids),
                }
            )
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.FILE,
                        record.file_id,
                        digest=record.sha256,
                    ),
                    role=ContextEntryRole.CONTEXT,
                    selection_reason="explicit canonical File/Artifact input reference",
                    inline_content=payload,
                    trust=ContextTrust.TRUSTED,
                    data_classification=_classification(record.classification),
                    priority=55,
                    relevance=0.9,
                    project_id=record.project_id,
                )
            )
        return tuple(candidates)


class MemoryContextSourceAdapter:
    adapter_id = "platform.context.memory/v1"

    def __init__(
        self,
        memory: MemoryProvider,
        access_context: Callable[[ContextSourceRequest], DataAccessContext],
        *,
        limit: int = 20,
    ) -> None:
        self.memory = memory
        self.access_context = access_context
        self.limit = limit

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        context = self.access_context(request)
        queries = (
            MemoryQuery(MemoryScope.TASK, request.task_id, limit=self.limit),
            MemoryQuery(MemoryScope.AGENT, request.agent_id, limit=self.limit),
        )
        candidates: list[ContextCandidate] = []
        for query in queries:
            for entry in await self.memory.query_entries(query, context):
                payload = _json(entry.value)
                candidates.append(
                    ContextCandidate(
                        source=ContextSourceRef(
                            ContextSourceType.MEMORY,
                            entry.memory_id,
                            digest=_sha(payload),
                        ),
                        role=ContextEntryRole.CONTEXT,
                        selection_reason=f"canonical {entry.scope.value} Memory entry",
                        inline_content=payload,
                        freshness=(
                            ContextFreshness.STALE if entry.expired else ContextFreshness.CURRENT
                        ),
                        trust=(
                            ContextTrust.TRUSTED
                            if entry.origin.value == "user-authored"
                            else ContextTrust.UNTRUSTED
                        ),
                        data_classification=_classification(entry.classification),
                        priority=25,
                        relevance=0.5,
                        project_id=request.project_id,
                        workspace_id=request.workspace_id if entry.scope is MemoryScope.WORKSPACE else None,
                    )
                )
        return tuple(candidates)


class KnowledgeContextSourceAdapter:
    adapter_id = "platform.context.knowledge/v1"

    def __init__(
        self,
        knowledge: KnowledgeProvider,
        access_context: Callable[[ContextSourceRequest], DataAccessContext],
        query_text: Callable[[ContextSourceRequest], str],
        *,
        limit: int = 10,
    ) -> None:
        self.knowledge = knowledge
        self.access_context = access_context
        self.query_text = query_text
        self.limit = limit

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        query = self.query_text(request).strip()
        if not query:
            return ()
        results = await self.knowledge.search(
            KnowledgeSearchRequest(
                query=query,
                context=self.access_context(request),
                mode=KnowledgeSearchMode.KEYWORD,
                limit=self.limit,
            )
        )
        return tuple(
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.KNOWLEDGE,
                    item.source_id,
                    revision=item.revision,
                    digest=_sha(item.content),
                    locator=item.location,
                ),
                role=ContextEntryRole.EVIDENCE,
                selection_reason="canonical Knowledge retrieval result",
                inline_content=item.content,
                trust=ContextTrust.UNTRUSTED,
                data_classification=_classification(item.classification),
                priority=30,
                relevance=max(0.0, min(1.0, item.score if item.score is not None else 0.5)),
                project_id=request.project_id,
                workspace_id=request.workspace_id,
            )
            for item in results
        )


@dataclass(frozen=True, slots=True)
class GuardedContextSourceAdapter:
    """Normalize provider failure into explicit unavailable context evidence."""

    adapter: ContextSourceAdapter
    unavailable_source_type: ContextSourceType
    unavailable_source_id: str
    mandatory_on_failure: bool = False

    @property
    def adapter_id(self) -> str:
        return f"guarded:{self.adapter.adapter_id}"

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        try:
            return await self.adapter.collect(request)
        except Exception as exc:
            marker = f"{self.adapter.adapter_id}:{type(exc).__name__}"
            return (
                ContextCandidate(
                    source=ContextSourceRef(
                        self.unavailable_source_type,
                        self.unavailable_source_id,
                        digest=_sha(marker),
                    ),
                    role=ContextEntryRole.CONTEXT,
                    selection_reason=f"source adapter unavailable: {self.adapter.adapter_id}",
                    mandatory=self.mandatory_on_failure,
                    content_ref=f"context-unavailable:{self.adapter.adapter_id}",
                    content_digest=_sha(marker),
                    freshness=ContextFreshness.UNAVAILABLE,
                    trust=ContextTrust.UNKNOWN,
                    data_classification=ContextDataClassification.INTERNAL,
                    project_id=request.project_id,
                    workspace_id=request.workspace_id,
                    metadata={"error_type": type(exc).__name__},
                ),
            )


__all__ = [
    "AgentContextSourceAdapter",
    "FileReferenceContextSourceAdapter",
    "GuardedContextSourceAdapter",
    "KnowledgeContextSourceAdapter",
    "MemoryContextSourceAdapter",
    "PlanStepContextSourceAdapter",
    "RepositoryContextSlice",
    "RepositoryIntelligenceContextSourceAdapter",
    "ResearchEvidenceContextSourceAdapter",
    "SkillBundleContextSourceAdapter",
    "TaskContextSourceAdapter",
]
