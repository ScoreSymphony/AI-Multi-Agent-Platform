from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed.capability_provider import (
    DistributedExecutorEchoProvider,
)
from ai_multi_agent_platform.distributed.executor_worker import (
    ExecutorWorker,
    executor_worker_input,
)
from ai_multi_agent_platform.distributed.models import (
    JobRequirements,
    NodeRecord,
    RegistrationRequest,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.registry import DistributedRegistry
from ai_multi_agent_platform.distributed.runtime import DistributedRuntime
from ai_multi_agent_platform.distributed.tool_lineage import tool_lineage
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution import ReferenceExecutor
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


def _context(task_id: str) -> OperationContext:
    return OperationContext(correlation_id=task_id)


def test_executor_worker_keeps_multiple_subjobs_distinct_within_one_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        run_id = new_id("run")
        worker_id = new_id("worker")
        root = tmp_path / "executor"
        (root / "reference").mkdir(parents=True)
        worker = ExecutorWorker(
            worker_id,
            ReferenceExecutor(root),
            workspace="reference",
        )

        def job(message: str) -> WorkerJobRequest:
            return WorkerJobRequest(
                worker_job_id=new_id("worker_job"),
                execution=ExecutionRequest(
                    run_id=run_id,
                    subject_type="task",
                    subject_id=task_id,
                    context=_context(task_id),
                    input=executor_worker_input(
                        action="echo",
                        arguments={"text": message},
                    ),
                ),
            )

        first = job("first")
        second = job("second")
        await worker.dispatch(first)
        await worker.dispatch(second)

        first_result = await worker.result(first.worker_job_id)
        second_result = await worker.result(second.worker_job_id)
        assert first_result is not None and first_result.execution is not None
        assert second_result is not None and second_result.execution is not None
        assert first_result.worker_job_id != second_result.worker_job_id
        assert first_result.execution.run_id == second_result.execution.run_id == run_id
        assert first_result.execution.output == {"text": "first"}
        assert second_result.execution.output == {"text": "second"}

    asyncio.run(scenario())


def test_capability_invocation_crosses_executor_and_exact_worker_with_root_trace(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        run_id = new_id("run")
        agent_id = new_id("agent")
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        snapshot_id = new_id("workspace_snapshot")
        node_id = new_id("node")
        worker_id = new_id("worker")

        registry = DistributedRegistry()
        runtime = DistributedRuntime(registry)
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(node_id=node_id, display_name="issue-46-node"),
                workers=(
                    WorkerRecord(
                        worker_id=worker_id,
                        node_id=node_id,
                        supported_executors=("reference",),
                        capability_refs=(ECHO_CAPABILITY_ID,),
                    ),
                ),
            )
        )
        root = tmp_path / "executor"
        (root / "reference").mkdir(parents=True)
        runtime.attach_worker(
            ExecutorWorker(
                worker_id,
                ReferenceExecutor(root),
                workspace="reference",
            )
        )

        bindings = InMemoryRunWorkspaceBindingRepository()
        await bindings.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=task_id,
                workspace_id=workspace_id,
                workspace_snapshot_id=snapshot_id,
                content_checksum="0" * 64,
            )
        )
        provider = DistributedExecutorEchoProvider(
            runtime,
            worker_id=worker_id,
            workspace_bindings=bindings,
        )
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(provider)
        result = await CapabilityInvoker(
            capabilities,
            canonical_binding_hook=bind_canonical_capability_invocation,
        ).invoke(
            CapabilityInvocation(
                invocation_id=f"{run_id}:tool-call-1",
                capability_id=ECHO_CAPABILITY_ID,
                version="1.0",
                arguments={"message": "through worker"},
                context=OperationContext(
                    correlation_id=task_id,
                    owner_type="service",
                    owner_id="issue-46-conformance",
                    project_id=project_id,
                ),
                trace=InvocationTrace(
                    correlation_id=task_id,
                    task_id=task_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    project_id=project_id,
                ),
            )
        )

        assert result.output == {"message": "through worker"}
        assert result.canonical_tool_invocation_id is not None
        assert len(result.evidence_refs) == 1
        worker_job_id = result.evidence_refs[0]
        record = runtime.get_record(worker_job_id)
        lineage = tool_lineage(record.job)
        assert lineage.root_run_id == run_id
        assert lineage.tool_invocation_id == result.canonical_tool_invocation_id
        assert lineage.correlation_id == task_id
        assert lineage.task_id == task_id
        assert record.job.execution.run_id == run_id
        assert record.job.execution.subject_id == task_id
        assert record.job.requirements == JobRequirements(
            executor_type="reference",
            capability_refs=(ECHO_CAPABILITY_ID,),
            preferred_worker_ids=(worker_id,),
        )
        assert record.job.workspace_ref == workspace_id
        assert record.job.snapshot_ref == snapshot_id
        assert record.worker_id == worker_id
        assert record.handle is not None
        metadata = result.adapter_metadata[0]
        assert metadata.namespace == "distributed-capability"
        assert metadata.values["tool_invocation_id"] == result.canonical_tool_invocation_id
        assert metadata.values["worker_job_id"] == worker_job_id
        assert metadata.values["worker_id"] == worker_id
        assert metadata.values["node_id"] == node_id
        assert metadata.values["task_id"] == task_id
        assert metadata.values["run_id"] == run_id
        assert metadata.values["agent_id"] == agent_id
        assert metadata.values["workspace_id"] == workspace_id
        assert metadata.values["workspace_snapshot_id"] == snapshot_id

    asyncio.run(scenario())
