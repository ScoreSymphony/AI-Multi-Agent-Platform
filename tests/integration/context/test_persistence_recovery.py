"""Context persistence/restart coverage originally introduced for GitHub issue #590."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextBudget,
    ContextCandidate,
    ContextEntryRole,
    ContextFreshness,
    ContextResolver,
    ContextRunBinding,
    ContextSourceRef,
    ContextSourceType,
    JsonContextBundleRepository,
    JsonContextRunBindingRepository,
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


def test_bundle_and_run_binding_survive_restart_and_reassembly(tmp_path) -> None:
    candidate = _inline_candidate(ContextSourceType.TASK, "restart-task", "restart-safe context")
    request = _request((candidate,))
    bundle_path = tmp_path / "context-bundles.json"
    binding_path = tmp_path / "context-run-bindings.json"

    first_repository = JsonContextBundleRepository(bundle_path)
    first_service = ContextAssemblyService(
        ContextResolver(FakeAuthorizationProvider()),
        first_repository,
    )
    first = asyncio.run(first_service.assemble(request))

    restarted_repository = JsonContextBundleRepository(bundle_path)
    restarted_service = ContextAssemblyService(
        ContextResolver(FakeAuthorizationProvider()),
        restarted_repository,
    )
    restored = asyncio.run(restarted_service.assemble(request))

    assert restored.context_bundle_id == first.context_bundle_id
    assert restored.digest == first.digest

    binding = ContextRunBinding(
        agent_run_id=new_id("agent_run"),
        run_id=first.run_id,
        task_id=first.task_id,
        agent_id=first.agent_id,
        agent_revision=first.agent_revision,
        context_bundle_id=first.context_bundle_id,
        context_bundle_digest=first.digest,
        resolver_version=first.resolver_version,
        policy_version=first.policy_version,
        orchestrator_adapter_id="reference-context-orchestrator",
        created_at=datetime.now(UTC),
    )
    JsonContextRunBindingRepository(binding_path).put(binding)

    restarted_bindings = JsonContextRunBindingRepository(binding_path)
    assert restarted_bindings.get(binding.agent_run_id) == binding
    assert restarted_bindings.list_for_run(first.run_id) == (binding,)
