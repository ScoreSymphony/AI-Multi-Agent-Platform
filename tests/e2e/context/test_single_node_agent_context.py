"""Single-node canonical Context E2E coverage originating in issue #650."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import AgentProfile
from ai_multi_agent_platform.deployment import SingleNodeReferenceDeployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import AuthorizationService


def test_public_single_node_agent_run_executes_with_canonical_context_and_survives_restart(
    tmp_path: Path,
) -> None:
    authorization = AuthorizationService()
    first = SingleNodeReferenceDeployment(
        data_root=tmp_path,
        authorization_provider=authorization,
    )
    owner_ref = OwnerRef(type="user", id="issue-650-owner")
    task = first.task_service.create(
        owner_ref=owner_ref,
        project_id="project-650",
        workspace_id="workspace-650",
        user_objective="Summarize the approved context.",
    )
    agent = first.agent_service.create_agent(
        AgentProfile(name="Issue 650 worker", role="worker"),
        owner_ref=owner_ref,
    )
    bundle = asyncio.run(
        first.context_service.assemble(
            first.task_context_adapter.make_request(
                task_id=task.task_id,
                run_id="run-650-public",
                agent_id=agent.agent_id,
                agent_revision=agent.revision,
                actor=task.owner,
                correlation_id="issue-650-public-path",
                project_id=task.project_id,
                workspace_id=task.workspace_id,
            )
        )
    )
    run = asyncio.run(
        first.agent_run_service.start(
            task_id=task.task_id,
            agent_id=agent.agent_id,
            context_bundle_id=bundle.context_bundle_id,
        )
    )
    assert run.context_bundle_id == bundle.context_bundle_id
    assert run.context_bundle_digest == bundle.digest
    persisted_run = first.kernel.run_repository.get(run.agent_run_id)
    assert persisted_run.context_bundle_id == bundle.context_bundle_id
    assert persisted_run.context_bundle_digest == bundle.digest
    assert first.context_binding_repository.get(run.agent_run_id) is not None

    raw_runs = (tmp_path / "kernel" / "agent_runs.json").read_text(encoding="utf-8")
    assert bundle.context_bundle_id in raw_runs
    assert bundle.digest in raw_runs
    raw_bindings = (tmp_path / "context" / "run_bindings.json").read_text(encoding="utf-8")
    assert bundle.context_bundle_id in raw_bindings
    raw_bundles = (tmp_path / "context" / "bundles.json").read_text(encoding="utf-8")
    assert bundle.context_bundle_id in raw_bundles

    second = SingleNodeReferenceDeployment(
        data_root=tmp_path,
        authorization_provider=authorization,
    )
    reloaded_run = second.kernel.run_repository.get(run.agent_run_id)
    assert reloaded_run.context_bundle_id == bundle.context_bundle_id
    assert reloaded_run.context_bundle_digest == bundle.digest
    reloaded_bundle = second.context_bundle_repository.get(bundle.context_bundle_id)
    reloaded_binding = second.context_binding_repository.get(run.agent_run_id)
    assert reloaded_bundle.digest == bundle.digest
    assert reloaded_binding.context_bundle_id == bundle.context_bundle_id
    assert reloaded_binding.context_bundle_digest == bundle.digest

    equivalent = asyncio.run(
        second.context_service.assemble(
            second.task_context_adapter.make_request(
                task_id=task.task_id,
                run_id=bundle.run_id,
                agent_id=agent.agent_id,
                agent_revision=agent.revision,
                actor=task.owner,
                correlation_id="issue-650-public-path-retry",
                project_id=task.project_id,
                workspace_id=task.workspace_id,
            )
        )
    )
    assert equivalent.context_bundle_id == bundle.context_bundle_id
    assert equivalent.digest == bundle.digest
