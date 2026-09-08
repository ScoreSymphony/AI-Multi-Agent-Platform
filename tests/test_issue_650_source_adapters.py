from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from ai_multi_agent_platform.context import (
    AgentContextSourceAdapter,
    ContextEntryRole,
    ContextSourceType,
    ContextTrust,
    FileArtifactResultContextSourceAdapter,
    KnowledgeContextSourceAdapter,
    MemoryContextSourceAdapter,
    OperationalContextSourceRequest,
    RepositoryContextSourceAdapter,
    ResearchEvidenceContextSourceAdapter,
    SkillBundleContextSourceAdapter,
    TaskContextSourceAdapter,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.research import EvidenceFreshness
from ai_multi_agent_platform.skills import SkillBundle


class _TaskRepository:
    def __init__(self, state) -> None:
        self.state = state

    async def get_task(self, task_id: str):
        assert task_id == self.state.task_id
        return self.state


class _RunRepository:
    def __init__(self, run) -> None:
        self.run = run

    async def get_run(self, task_id: str, run_id: str):
        assert task_id == self.run.task_id
        assert run_id == self.run.run_id
        return self.run


class _AgentService:
    def __init__(self, revision) -> None:
        self.revision = revision

    def get_agent_revision(self, agent_id: str, revision: int | None = None):
        assert agent_id == self.revision.agent_id
        assert revision in {None, self.revision.revision}
        return self.revision


class _SkillRepository:
    def __init__(self, bundle: SkillBundle) -> None:
        self.bundle = bundle

    def list_bundles(self, *, run_id: str | None = None):
        if run_id is not None and run_id != self.bundle.run_id:
            return ()
        return (self.bundle,)

    def get_skill_revision(self, skill_id: str, revision: int):
        raise AssertionError(f"empty Skill Bundle must not resolve {skill_id}@{revision}")


class _ResearchRepository:
    def __init__(self, item, claim, evidence) -> None:
        self.item = item
        self.claim = claim
        self.evidence = evidence

    def list_items(self):
        return (self.item,)

    def list_evidence(self, research_item_id: str):
        assert research_item_id == self.item.research_item_id
        return (self.evidence,)

    def get_claim(self, claim_id: str):
        assert claim_id == self.claim.claim_id
        return self.claim


class _ResearchService:
    def __init__(self, repository: _ResearchRepository) -> None:
        self.repository = repository

    def evidence_freshness(self, evidence_id: str) -> EvidenceFreshness:
        assert evidence_id == self.repository.evidence.evidence_id
        return EvidenceFreshness.CURRENT


class _RepositoryProvenance:
    def __init__(self, records=()) -> None:
        self.records = tuple(records)

    def for_run(self, run_id: str):
        del run_id
        return self.records


class _RepositoryService:
    def __init__(self, repository_id: str, revision: str) -> None:
        self.repository_id = repository_id
        self.revision = revision

    async def read_tree(self, repository_id, revision, context, **limits):
        del context, limits
        assert repository_id == self.repository_id
        assert revision == self.revision
        return SimpleNamespace(
            resolved_revision=self.revision,
            entries=(
                SimpleNamespace(
                    relative_path="src/example.py",
                    data=b"line one\nline two\nline three\nline four\n",
                ),
            ),
        )


class _FileProvider:
    def __init__(self, record, payload: bytes) -> None:
        self.record = record
        self.payload = payload

    async def list_files(self, context):
        del context
        return (self.record,)

    async def stream_file(self, file_id: str, context):
        del context
        assert file_id == self.record.file_id
        yield self.payload


class _MemoryProvider:
    def __init__(self, entry) -> None:
        self.entry = entry

    async def query_entries(self, query, context):
        del context
        return (self.entry,) if query.scope is MemoryScope.TASK else ()


class _KnowledgeProvider:
    def __init__(self, result) -> None:
        self.result = result
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return (self.result,)


def _request() -> OperationalContextSourceRequest:
    task_id = new_id("task")
    project_id = new_id("project")
    return OperationalContextSourceRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=2,
        project_id=project_id,
        workspace_id=new_id("workspace"),
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-650",
            project_id=project_id,
        ),
        actor_ref="user:issue-650",
    )


def test_task_and_agent_adapters_preserve_canonical_revision_and_instruction_authority() -> None:
    request = _request()
    task = SimpleNamespace(
        id=request.task_id,
        title="Canonical task",
        description="Use exact platform context.",
        status=SimpleNamespace(value="running"),
        project_id=request.project_id,
        metadata={},
    )
    task_state = SimpleNamespace(task_id=request.task_id, task=task, revision=7)
    agent_revision = SimpleNamespace(
        agent_id=request.agent_id,
        revision=request.agent_revision,
        project_id=request.project_id,
        workspace_id=request.workspace_id,
        profile=SimpleNamespace(
            name="Issue 650 Agent",
            role="general_assistant",
            instructions=SimpleNamespace(
                role=SimpleNamespace(content="Follow the exact Task intent.", version="role-v3"),
                platform_constraint_refs=("platform:security",),
                project_instruction_refs=("project:instructions",),
            ),
        ),
    )

    task_candidate = asyncio.run(TaskContextSourceAdapter(_TaskRepository(task_state)).collect(request))[0]
    agent_candidate = asyncio.run(AgentContextSourceAdapter(_AgentService(agent_revision)).collect(request))[0]

    assert task_candidate.source.source_type is ContextSourceType.TASK
    assert task_candidate.source.revision == "7"
    assert task_candidate.mandatory is True
    assert agent_candidate.source.source_type is ContextSourceType.AGENT
    assert agent_candidate.source.revision == str(request.agent_revision)
    assert agent_candidate.role is ContextEntryRole.INSTRUCTION
    assert agent_candidate.mandatory is True
    assert "Follow the exact Task intent." in (agent_candidate.inline_content or "")


def test_skill_and_research_adapters_preserve_bundle_and_evidence_identity() -> None:
    request = _request()
    skill_bundle = SkillBundle(
        skill_bundle_id=new_id("skill_bundle"),
        digest="a" * 64,
        entries=(),
        resolver_version="skill-resolver/v1",
        policy_version="skill-policy/v1",
        run_id=request.run_id,
        task_id=request.task_id,
        agent_id=request.agent_id,
        agent_revision=request.agent_revision,
        project_id=request.project_id,
        workspace_id=request.workspace_id,
    )
    skill_candidate = asyncio.run(
        SkillBundleContextSourceAdapter(_SkillRepository(skill_bundle)).collect(request)
    )[0]

    evidence_digest = "b" * 64
    item = SimpleNamespace(
        research_item_id=new_id("research_item"),
        revision=4,
        run_id=request.run_id,
        task_id=request.task_id,
        data_class="internal",
        project_id=request.project_id,
        workspace_id=request.workspace_id,
    )
    claim = SimpleNamespace(
        claim_id=new_id("claim"),
        revision=3,
        text="The repository uses the canonical context boundary.",
        status=SimpleNamespace(value="supported"),
    )
    evidence = SimpleNamespace(
        evidence_id=new_id("evidence"),
        claim_id=claim.claim_id,
        relation=SimpleNamespace(value="supports"),
        location_ref="src/context.py:10-20",
        source_revision="source-r2",
        source_version=None,
        source_commit="deadbeef",
        source_etag=None,
        source_snapshot_digest="c" * 64,
        artifact_id=None,
        digest=evidence_digest,
    )
    research_candidate = asyncio.run(
        ResearchEvidenceContextSourceAdapter(
            _ResearchService(_ResearchRepository(item, claim, evidence))  # type: ignore[arg-type]
        ).collect(request)
    )[0]

    assert skill_candidate.source.source_id == skill_bundle.skill_bundle_id
    assert skill_candidate.source.digest == skill_bundle.digest
    assert skill_candidate.role is ContextEntryRole.INSTRUCTION
    assert research_candidate.source.source_id == evidence.evidence_id
    assert research_candidate.source.revision == "deadbeef"
    assert research_candidate.source.digest == evidence_digest
    assert research_candidate.source.locator == "src/context.py:10-20"
    assert research_candidate.role is ContextEntryRole.EVIDENCE
    assert research_candidate.trust is ContextTrust.UNTRUSTED


def test_repository_adapter_preserves_run_provenance_and_exact_source_slice() -> None:
    request = _request()
    repository_id = new_id("repository")
    revision = "git-abc123"
    provenance_record = SimpleNamespace(
        repository_id=repository_id,
        input_revision=revision,
        branch_ref="refs/heads/main",
        output_revision=None,
        diff_artifact_ids=(),
        provider_resource_ids=(),
    )
    task = SimpleNamespace(
        metadata={
            "context.repository_slices": [
                {
                    "repository_id": repository_id,
                    "path": "src/example.py",
                    "revision": revision,
                    "start_line": 2,
                    "end_line": 3,
                }
            ]
        }
    )
    task_state = SimpleNamespace(task_id=request.task_id, task=task)
    adapter = RepositoryContextSourceAdapter(
        _RepositoryProvenance((provenance_record,)),  # type: ignore[arg-type]
        repositories=_RepositoryService(repository_id, revision),  # type: ignore[arg-type]
        tasks=_TaskRepository(task_state),  # type: ignore[arg-type]
    )

    candidates = asyncio.run(adapter.collect(request))
    provenance = next(item for item in candidates if item.trust is ContextTrust.TRUSTED)
    source_slice = next(item for item in candidates if item.trust is ContextTrust.UNTRUSTED)

    assert provenance.source.source_id == repository_id
    assert provenance.source.revision == revision
    assert source_slice.source.revision == revision
    assert source_slice.source.locator == "src/example.py:2-3"
    assert source_slice.inline_content == "line two\nline three"
    assert source_slice.role is ContextEntryRole.CONTEXT


def test_file_artifact_result_adapter_keeps_file_payload_bounded_and_refs_explicit() -> None:
    request = _request()
    artifact_id = new_id("artifact")
    result_id = new_id("result")
    file_id = new_id("file")
    payload = b"canonical file context\n"
    file_record = SimpleNamespace(
        file_id=file_id,
        artifact_ids=(artifact_id,),
        classification="internal",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/plain",
        project_id=request.project_id,
    )
    task = SimpleNamespace(metadata={})
    task_state = SimpleNamespace(
        task_id=request.task_id,
        task=task,
        artifact_ids=(artifact_id,),
        result_ids=(result_id,),
    )
    run = SimpleNamespace(
        task_id=request.task_id,
        run_id=request.run_id,
        artifact_ids=(),
        result_ids=(),
    )
    adapter = FileArtifactResultContextSourceAdapter(
        _FileProvider(file_record, payload),  # type: ignore[arg-type]
        _TaskRepository(task_state),  # type: ignore[arg-type]
        _RunRepository(run),  # type: ignore[arg-type]
    )

    candidates = asyncio.run(adapter.collect(request))
    by_type = {item.source.source_type: item for item in candidates}

    assert set(by_type) == {
        ContextSourceType.FILE,
        ContextSourceType.ARTIFACT,
        ContextSourceType.RESULT,
    }
    assert by_type[ContextSourceType.FILE].source.source_id == file_id
    assert by_type[ContextSourceType.FILE].source.digest == file_record.sha256
    assert by_type[ContextSourceType.FILE].inline_content == payload.decode("utf-8")
    assert artifact_id in (by_type[ContextSourceType.ARTIFACT].inline_content or "")
    assert result_id in (by_type[ContextSourceType.RESULT].inline_content or "")


def test_memory_and_knowledge_adapters_use_canonical_scope_and_agent_source_allowlist() -> None:
    request = _request()
    memory_entry = SimpleNamespace(
        memory_id=new_id("memory"),
        classification="internal",
        value={"note": "remember exact bundle identity"},
        scope=MemoryScope.TASK,
        expired=False,
        created_by=request.actor_ref,
    )
    memory_candidates = asyncio.run(
        MemoryContextSourceAdapter(_MemoryProvider(memory_entry)).collect(request)  # type: ignore[arg-type]
    )

    knowledge_source_id = new_id("knowledge_source")
    agent_revision = SimpleNamespace(
        agent_id=request.agent_id,
        revision=request.agent_revision,
        profile=SimpleNamespace(
            data_access=SimpleNamespace(knowledge_source_ids=(knowledge_source_id,))
        ),
    )
    knowledge_result = SimpleNamespace(
        document_id=new_id("knowledge_document"),
        revision="knowledge-r4",
        location="knowledge://context/operationalization",
        content="Canonical Context is the only execution context authority.",
        classification="internal",
        score=0.9,
    )
    knowledge_provider = _KnowledgeProvider(knowledge_result)
    knowledge_candidates = asyncio.run(
        KnowledgeContextSourceAdapter(
            knowledge_provider,  # type: ignore[arg-type]
            _AgentService(agent_revision),  # type: ignore[arg-type]
        ).collect(request)
    )

    assert len(memory_candidates) == 1
    assert memory_candidates[0].source.source_type is ContextSourceType.MEMORY
    assert memory_candidates[0].trust is ContextTrust.TRUSTED
    assert len(knowledge_candidates) == 1
    assert knowledge_candidates[0].source.source_type is ContextSourceType.KNOWLEDGE
    assert knowledge_candidates[0].source.revision == "knowledge-r4"
    assert knowledge_candidates[0].trust is ContextTrust.UNTRUSTED
    assert knowledge_provider.requests[0].source_ids == (knowledge_source_id,)
