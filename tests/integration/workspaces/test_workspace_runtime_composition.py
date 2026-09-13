from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.automation import (
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.control_plane.automation_runtime_composition import ControlPlane
from ai_multi_agent_platform.domain import Event, OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


def test_runtime_composition_wires_run_workspace_binding_scope_into_automation() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        )
        bindings = InMemoryRunWorkspaceBindingRepository()
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        run_id = new_id("run")
        await bindings.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=new_id("task"),
                workspace_id=workspace_id,
                workspace_snapshot_id=new_id("workspace_snapshot"),
                content_checksum="b" * 64,
            )
        )

        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            run_workspace_bindings=bindings,
        )
        now = datetime(2026, 9, 4, 17, 0, tzinfo=UTC)
        automation = await control_plane.automation_service.create_automation(
            name="runtime workspace scope",
            description="",
            identity=IdentityContext(
                principal_ref="user:runtime-workspace-owner",
                owner_type="user",
                owner_id="runtime-workspace-owner",
            ),
            project_id=project_id,
            workspace_id=workspace_id,
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="run.completed",
            ),
            task_template=TaskTemplate(
                title="Runtime workspace event",
                objective="Prove production runtime resolver composition",
            ),
            now=now,
        )

        deliveries = await control_plane.automation_service.deliver_canonical_platform_event(
            Event(
                event_type="run.completed",
                subject_type="run",
                subject_id=run_id,
                correlation_id="issue-241-runtime-workspace",
                owner_ref=OwnerRef(type="user", id="runtime-workspace-owner"),
                project_id=project_id,
                occurred_at=now + timedelta(seconds=1),
            )
        )
        assert len(deliveries) == 1
        assert deliveries[0].automation_id == automation.id

    asyncio.run(scenario())
