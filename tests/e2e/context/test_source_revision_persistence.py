"""Historical Context bundle persistence E2E coverage originating in issue #680."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS, bootstrap_standard_agents
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextBudget,
    ContextLifecycleSourceRequest,
    ContextSourceType,
    VerificationContextSourceAdapter,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.context_verification import (
    CanonicalVerificationContextClassificationResolver,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType


def test_public_single_node_real_adapters_preserve_historical_bundle_across_source_revision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        data_dir = tmp_path / "issue-680-conformance"
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        project = deployment.scopes.create_project(
            key="issue-680-project",
            name="Issue 680 context project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-680-workspace",
            project_id=project.id,
        )
        bootstrap_standard_agents(deployment.agents)
        assistant = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
            workspace_id=workspace.id,
            name="Issue 680 Context Assistant",
        )
        task = await deployment.kernel.create_task(
            idempotency_key="issue-680-create-task",
            title="Context revision proof",
            objective="First canonical user objective.",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
            actor_ref=admin.user_id,
        )
        run_id = new_id("run")
        operation = OperationContext(
            correlation_id=task.task_id,
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        source = ContextLifecycleSourceRequest(
            execution=SimpleNamespace(),
            task_id=task.task_id,
            run_id=run_id,
            agent_id=assistant.agent_id,
            agent_revision=assistant.revision,
            project_id=project.id,
            workspace_id=workspace.id,
            plan_id=None,
            step_id=None,
            objective=task.task.description,
            execution_binding=None,
        )
        binding_factory = deployment.context.lifecycle._binding_factory  # noqa: SLF001
        bindings = tuple(binding_factory(source))
        verification_binding = next(
            binding
            for binding in bindings
            if isinstance(binding.adapter, VerificationContextSourceAdapter)
            and binding.source_type is ContextSourceType.VERIFICATION
        )
        assert isinstance(
            verification_binding.adapter.classification_resolver,
            CanonicalVerificationContextClassificationResolver,
        )

        def assembly_request() -> ContextAssemblyRequest:
            return ContextAssemblyRequest(
                task_id=task.task_id,
                run_id=run_id,
                agent_id=assistant.agent_id,
                agent_revision=assistant.revision,
                actor=ActorIdentity(actor_id=admin.user_id, actor_type=ActorType.HUMAN),
                operation=operation,
                candidates=(),
                budget=ContextBudget(
                    max_tokens=64_000,
                    max_bytes=256 * 1024,
                    max_items=128,
                ),
                workspace_id=workspace.id,
            )

        first = await deployment.context.assembly.assemble(
            assembly_request(),
            bindings=bindings,
        )
        first_task_entry = next(
            entry for entry in first.entries if entry.source.source_type is ContextSourceType.TASK
        )
        assert first_task_entry.source.revision == "1"

        updated = await deployment.kernel.update_task(
            idempotency_key="issue-680-update-task",
            task_id=task.task_id,
            objective="Second canonical user objective after an explicit Task revision.",
            actor_ref=admin.user_id,
        )
        assert updated.revision > task.revision

        second = await deployment.context.assembly.assemble(
            assembly_request(),
            bindings=bindings,
        )
        second_task_entry = next(
            entry for entry in second.entries if entry.source.source_type is ContextSourceType.TASK
        )
        assert second_task_entry.source.revision == str(updated.revision)
        assert second.digest != first.digest
        assert second.context_bundle_id != first.context_bundle_id

        historical = deployment.context.bundles.get(first.context_bundle_id)
        assert historical.digest == first.digest
        historical_task_entry = next(
            entry
            for entry in historical.entries
            if entry.source.source_type is ContextSourceType.TASK
        )
        assert historical_task_entry.source.revision == "1"

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False)
        )
        assert restarted.context.bundles.get(first.context_bundle_id).digest == first.digest
        assert restarted.context.bundles.get(second.context_bundle_id).digest == second.digest

    asyncio.run(scenario())
