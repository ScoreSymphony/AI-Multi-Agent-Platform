"""Context adapter/rendering integration coverage introduced for GitHub issue #590."""

from __future__ import annotations

import asyncio
import hashlib

from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextBudget,
    ContextCandidate,
    ContextEntryRole,
    ContextFreshness,
    ContextResolver,
    ContextSourceRef,
    ContextSourceType,
    InMemoryContextBundleRepository,
    ReferenceContextRenderer,
    StaticContextSourceAdapter,
    assert_render_preserves_bundle,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


def _inline_candidate(
    source_type: ContextSourceType,
    source_id: str,
    content: str,
    *,
    mandatory: bool = False,
    role: ContextEntryRole = ContextEntryRole.CONTEXT,
    revision: str = "r1",
    freshness: ContextFreshness = ContextFreshness.CURRENT,
    project_id: str | None = None,
    workspace_id: str | None = None,
    priority: int = 0,
    relevance: float = 0.5,
    locator: str | None = None,
) -> ContextCandidate:
    source_digest = hashlib.sha256(f"{source_id}@{revision}".encode()).hexdigest()
    return ContextCandidate(
        source=ContextSourceRef(
            source_type=source_type,
            source_id=source_id,
            revision=revision,
            digest=source_digest,
            locator=locator,
        ),
        role=role,
        selection_reason=f"include {source_id}",
        mandatory=mandatory,
        inline_content=content,
        freshness=freshness,
        priority=priority,
        relevance=relevance,
        project_id=project_id,
        workspace_id=workspace_id,
    )


def _request(
    candidates: tuple[ContextCandidate, ...],
    *,
    budget: ContextBudget | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    skill_bundle_id: str | None = None,
    skill_bundle_digest: str | None = None,
) -> ContextAssemblyRequest:
    return ContextAssemblyRequest(
        task_id=new_id("task"),
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        actor=ActorIdentity("user:issue-590", ActorType.HUMAN),
        operation=OperationContext(
            correlation_id="issue-590-context",
            project_id=project_id,
        ),
        candidates=candidates,
        budget=budget or ContextBudget(max_tokens=4096, max_bytes=16384, max_items=64),
        workspace_id=workspace_id,
        skill_bundle_id=skill_bundle_id,
        skill_bundle_digest=skill_bundle_digest,
    )


def _resolve(request: ContextAssemblyRequest):
    resolver = ContextResolver(FakeAuthorizationProvider())
    return asyncio.run(resolver.resolve(request))


def test_skill_research_and_repository_adapters_preserve_exact_provenance() -> None:
    project_id = new_id("project")
    skill = _inline_candidate(
        ContextSourceType.SKILL,
        "skill:review",
        "skill instructions",
        revision="skill-r7",
        locator="skills/review.md",
        project_id=project_id,
    )
    research = _inline_candidate(
        ContextSourceType.RESEARCH_EVIDENCE,
        "evidence:claim-42",
        "evidence excerpt",
        revision="evidence-r3",
        locator="claim:42/evidence:2",
        project_id=project_id,
    )
    repository = _inline_candidate(
        ContextSourceType.REPOSITORY,
        "repo:source-slice",
        "def canonical_context(): ...",
        revision="git:abc123",
        locator="src/context.py#L10-L20",
        project_id=project_id,
    )
    request = _request(
        (),
        project_id=project_id,
        skill_bundle_id="skill-bundle:issue-590",
        skill_bundle_digest="sha256:skill-bundle-digest",
    )
    service = ContextAssemblyService(
        ContextResolver(FakeAuthorizationProvider()),
        InMemoryContextBundleRepository(),
    )

    bundle = asyncio.run(
        service.assemble(
            request,
            adapters=(
                StaticContextSourceAdapter("repository", (repository,)),
                StaticContextSourceAdapter("skill", (skill,)),
                StaticContextSourceAdapter("research", (research,)),
            ),
        )
    )

    assert bundle.skill_bundle_id == "skill-bundle:issue-590"
    assert bundle.skill_bundle_digest == "sha256:skill-bundle-digest"
    by_type = {entry.source.source_type: entry for entry in bundle.entries}
    assert by_type[ContextSourceType.SKILL].source.revision == "skill-r7"
    assert by_type[ContextSourceType.SKILL].source.locator == "skills/review.md"
    assert by_type[ContextSourceType.RESEARCH_EVIDENCE].source.revision == "evidence-r3"
    assert by_type[ContextSourceType.RESEARCH_EVIDENCE].source.locator == "claim:42/evidence:2"
    assert by_type[ContextSourceType.REPOSITORY].source.revision == "git:abc123"
    assert by_type[ContextSourceType.REPOSITORY].source.locator == "src/context.py#L10-L20"


def test_renderer_replacement_preserves_canonical_bundle_identity() -> None:
    class AlternateRenderer(ReferenceContextRenderer):
        renderer_id = "alternate-context-renderer/v1"

    bundle = _resolve(
        _request(
            (
                _inline_candidate(
                    ContextSourceType.TASK,
                    "task-render",
                    "render me identically",
                    mandatory=True,
                ),
            )
        )
    )

    first = asyncio.run(ReferenceContextRenderer().render(bundle))
    second = asyncio.run(AlternateRenderer().render(bundle))

    assert_render_preserves_bundle(bundle, first)
    assert_render_preserves_bundle(bundle, second)
    assert first.renderer_id != second.renderer_id
    assert first.context_bundle_id == second.context_bundle_id == bundle.context_bundle_id
    assert first.context_bundle_digest == second.context_bundle_digest == bundle.digest
    assert [part.content_digest for part in first.parts] == [
        part.content_digest for part in second.parts
    ]
