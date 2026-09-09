"""Operational source adapters for canonical Context Bundle assembly.

Source domains remain authoritative. These adapters only project exact canonical state into
#590 Context candidates and never own Task, Agent, Skill, Research, Repository or data lifecycles.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.agents.execution_profile import (
    decode_agent_execution_binding,
    decode_agent_step_execution_binding,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.data.contracts import FileProvider, KnowledgeProvider, MemoryProvider
from ai_multi_agent_platform.data.models import (
    DataAccessContext,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    MemoryQuery,
    MemoryScope,
)
from ai_multi_agent_platform.kernel.repository import RunRepository, TaskRepository
from ai_multi_agent_platform.repositories.service import (
    RepositoryCallContext,
    RepositoryProvenanceStore,
    RepositoryService,
)
from ai_multi_agent_platform.research import EvidenceFreshness, ResearchService
from ai_multi_agent_platform.skills import ReferenceSkillRenderer, SkillBundle, SkillRepository

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
from .resolver import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextSourceAdapter,
    ContextSourceRequest,
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
_MAX_REPOSITORY_SLICE_LINES = 500
_MAX_REPOSITORY_TREE_ENTRIES = 5000
_MAX_REPOSITORY_TREE_BYTES = 8 * 1024 * 1024
_MAX_INLINE_FILE_BYTES = 64 * 1024


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _classification(value: DataClassification | str | None) -> ContextDataClassification:
    if value is None:
        return ContextDataClassification.INTERNAL
    try:
        normalized = value if isinstance(value, DataClassification) else DataClassification(value)
    except ValueError:
        return ContextDataClassification.RESTRICTED
    if normalized is DataClassification.PUBLIC:
        return ContextDataClassification.PUBLIC
    if normalized is DataClassification.INTERNAL:
        return ContextDataClassification.INTERNAL
    if normalized in {DataClassification.CONFIDENTIAL, DataClassification.PRIVATE}:
        return ContextDataClassification.CONFIDENTIAL
    if normalized in {DataClassification.RESTRICTED}:
        return ContextDataClassification.RESTRICTED
    return ContextDataClassification.SECRET_REFERENCE


def _operational_request(
    request: ContextSourceRequest,
) -> tuple[OperationContext, str]:
    operation = getattr(request, "operation", None)
    actor_ref = getattr(request, "actor_ref", None)
    if not isinstance(operation, OperationContext):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "operational Context source adapter requires OperationContext",
        )
    if not isinstance(actor_ref, str) or not actor_ref.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "operational Context source adapter requires actor_ref",
        )
    return operation, actor_ref


@dataclass(frozen=True, slots=True)
class OperationalContextSourceRequest:
    """Strict superset of #590 source request used only at operational composition."""

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
    """Normalize source availability, then delegate truth to canonical #590 assembly."""

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
                        availability="missing"
                        if exc.code is ErrorCode.NOT_FOUND
                        else "unavailable",
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


class RepositoryContextSourceAdapter:
    adapter_id = "platform.repository-context/v1"

    def __init__(
        self,
        provenance: RepositoryProvenanceStore,
        *,
        repositories: RepositoryService | None = None,
        tasks: TaskRepository | None = None,
    ) -> None:
        self.provenance = provenance
        self.repositories = repositories
        self.tasks = tasks

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = []
        provenance = tuple(
            sorted(self.provenance.for_run(request.run_id), key=lambda x: x.repository_id)
        )
        for record in provenance:
            content = _canonical_json(
                {
                    "repository_id": record.repository_id,
                    "input_revision": record.input_revision,
                    "branch_ref": record.branch_ref,
                    "output_revision": record.output_revision,
                    "diff_artifact_ids": list(record.diff_artifact_ids),
                    "provider_resource_ids": list(record.provider_resource_ids),
                }
            )
            digest = _digest(content)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.REPOSITORY,
                        record.repository_id,
                        revision=record.input_revision,
                        digest=digest,
                    ),
                    role=ContextEntryRole.CONTEXT,
                    selection_reason="exact repository Run input provenance",
                    inline_content=content,
                    content_digest=digest,
                    trust=ContextTrust.TRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=60,
                    relevance=0.7,
                    project_id=request.project_id,
                )
            )
        if self.repositories is None or self.tasks is None:
            return tuple(candidates)
        state = await self.tasks.get_task(request.task_id)
        raw_slices = state.task.metadata.get("context.repository_slices")
        if not isinstance(raw_slices, tuple | list):
            return tuple(candidates)
        operation, actor_ref = _operational_request(request)
        by_repository = {item.repository_id: item for item in provenance}
        for raw in raw_slices:
            if not isinstance(raw, Mapping):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "context.repository_slices entries must be objects",
                )
            repository_id = raw.get("repository_id")
            path = raw.get("path")
            start_line = raw.get("start_line", 1)
            end_line = raw.get("end_line", start_line + 199 if isinstance(start_line, int) else 200)
            if not isinstance(repository_id, str) or not repository_id.strip():
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "repository slice requires repository_id"
                )
            if not isinstance(path, str) or not path.strip():
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "repository slice requires path"
                )
            if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "repository slice start_line invalid"
                )
            if isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < start_line:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "repository slice end_line invalid"
                )
            if end_line - start_line + 1 > _MAX_REPOSITORY_SLICE_LINES:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "repository slice exceeds line limit"
                )
            explicit_revision = raw.get("revision")
            if explicit_revision is not None and (
                not isinstance(explicit_revision, str) or not explicit_revision.strip()
            ):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "repository slice revision invalid"
                )
            bound = by_repository.get(repository_id)
            revision = explicit_revision or (None if bound is None else bound.input_revision)
            if revision is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "repository slice has no exact Run provenance or explicit immutable revision",
                )
            tree = await self.repositories.read_tree(
                repository_id,
                revision,
                RepositoryCallContext(
                    operation=operation,
                    actor_ref=actor_ref,
                    task_id=request.task_id,
                    run_id=request.run_id,
                    agent_id=request.agent_id,
                ),
                max_entries=_MAX_REPOSITORY_TREE_ENTRIES,
                max_total_bytes=_MAX_REPOSITORY_TREE_BYTES,
            )
            entry = next((item for item in tree.entries if item.relative_path == path), None)
            if entry is None:
                raise ContractError(
                    ErrorCode.NOT_FOUND, f"repository source path not found: {path}"
                )
            try:
                text = entry.data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "repository Context source slice must be UTF-8 text",
                ) from exc
            lines = text.splitlines()
            selected = "\n".join(lines[start_line - 1 : end_line])
            if not selected:
                raise ContractError(ErrorCode.NOT_FOUND, "repository source slice is empty")
            digest = _digest(selected)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.REPOSITORY,
                        repository_id,
                        revision=tree.resolved_revision,
                        digest=digest,
                        locator=f"{path}:{start_line}-{end_line}",
                    ),
                    role=ContextEntryRole.CONTEXT,
                    selection_reason="explicit exact Repository Intelligence source slice",
                    inline_content=selected,
                    content_digest=digest,
                    trust=ContextTrust.UNTRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=55,
                    relevance=0.9,
                    project_id=request.project_id,
                )
            )
        return tuple(candidates)


class FileArtifactResultContextSourceAdapter:
    adapter_id = "platform.file-artifact-result-context/v1"

    def __init__(
        self,
        files: FileProvider,
        tasks: TaskRepository,
        runs: RunRepository,
        *,
        max_inline_file_bytes: int = _MAX_INLINE_FILE_BYTES,
    ) -> None:
        self.files = files
        self.tasks = tasks
        self.runs = runs
        self.max_inline_file_bytes = max_inline_file_bytes

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        operation, actor_ref = _operational_request(request)
        task = await self.tasks.get_task(request.task_id)
        run = await self.runs.get_run(request.task_id, request.run_id)
        access = DataAccessContext(
            operation=operation,
            actor_ref=actor_ref,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
        )
        refs: set[str] = (
            set(task.artifact_ids)
            | set(task.result_ids)
            | set(run.artifact_ids)
            | set(run.result_ids)
        )
        binding = (
            decode_agent_step_execution_binding(task.task.metadata, request.step_id)
            if request.step_id is not None
            else None
        ) or decode_agent_execution_binding(task.task.metadata)
        if binding is not None:
            refs.update(binding.input_refs)
            refs.update(binding.output_refs)

        candidates: list[ContextCandidate] = []
        files = await self.files.list_files(access)
        for file in sorted(files, key=lambda item: item.file_id):
            if file.file_id not in refs and not set(file.artifact_ids).intersection(refs):
                continue
            classification = _classification(file.classification)
            if classification is ContextDataClassification.SECRET_REFERENCE:
                continue
            if file.size_bytes <= self.max_inline_file_bytes:
                raw = bytearray()
                async for chunk in self.files.stream_file(file.file_id, access):
                    raw.extend(chunk)
                    if len(raw) > self.max_inline_file_bytes:
                        break
                try:
                    body = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    body = _canonical_json(
                        {
                            "file_id": file.file_id,
                            "sha256": file.sha256,
                            "size_bytes": file.size_bytes,
                            "content_type": file.content_type,
                            "artifact_ids": list(file.artifact_ids),
                            "payload": "binary-not-inlined",
                        }
                    )
            else:
                body = _canonical_json(
                    {
                        "file_id": file.file_id,
                        "sha256": file.sha256,
                        "size_bytes": file.size_bytes,
                        "content_type": file.content_type,
                        "artifact_ids": list(file.artifact_ids),
                        "payload": "large-file-not-inlined",
                    }
                )
            digest = _digest(body)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.FILE,
                        file.file_id,
                        digest=file.sha256,
                    ),
                    role=ContextEntryRole.CONTEXT,
                    selection_reason=(
                        "Run/Task-referenced canonical File content or bounded metadata"
                    ),
                    inline_content=body,
                    content_digest=digest,
                    trust=ContextTrust.UNTRUSTED,
                    data_classification=classification,
                    priority=35,
                    relevance=0.75,
                    project_id=file.project_id,
                )
            )
        for artifact_id in sorted(ref for ref in refs if ref.startswith("artifact_")):
            linked_files = sorted(
                file.file_id for file in files if artifact_id in file.artifact_ids
            )
            body = _canonical_json(
                {
                    "artifact_id": artifact_id,
                    "file_ids": cast(JsonValue, linked_files),
                }
            )
            digest = _digest(body)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(ContextSourceType.ARTIFACT, artifact_id, digest=digest),
                    role=ContextEntryRole.EVIDENCE,
                    selection_reason="canonical Artifact reference linked to this Task/Run",
                    inline_content=body,
                    content_digest=digest,
                    trust=ContextTrust.TRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=30,
                    relevance=0.65,
                    project_id=request.project_id,
                )
            )
        for result_id in sorted(ref for ref in refs if ref.startswith("result_")):
            body = _canonical_json({"result_id": result_id, "run_id": request.run_id})
            digest = _digest(body)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(ContextSourceType.RESULT, result_id, digest=digest),
                    role=ContextEntryRole.EVIDENCE,
                    selection_reason="canonical Result reference linked to this Task/Run",
                    inline_content=body,
                    content_digest=digest,
                    trust=ContextTrust.TRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=30,
                    relevance=0.65,
                    project_id=request.project_id,
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
