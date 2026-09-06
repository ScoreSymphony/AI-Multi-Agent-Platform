from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed.models import WorkerJobRequest
from ai_multi_agent_platform.distributed.tool_lineage import (
    bind_worker_job_to_tool_invocation,
    tool_lineage,
    worker_job_id_for_tool_invocation,
)
from ai_multi_agent_platform.distributed.transport import WorkerTransportCodec
from ai_multi_agent_platform.domain import new_id


def _job() -> WorkerJobRequest:
    task_id = new_id("task")
    run_id = new_id("run")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id="issue-46-worker-tool-lineage",
                causation_id="model-call-before-tool",
            ),
            input={"objective": "execute one tool call"},
        )
    )


def test_tool_worker_job_keeps_root_run_and_uses_worker_job_as_child_identity() -> None:
    original = _job()
    tool_invocation_id = new_id("tool_invocation")

    bound = bind_worker_job_to_tool_invocation(original, tool_invocation_id)
    lineage = tool_lineage(bound)

    assert bound.execution.run_id == original.execution.run_id
    assert bound.execution.subject_id == original.execution.subject_id
    assert bound.execution.context.correlation_id == original.execution.context.correlation_id
    assert bound.execution.context.causation_id == tool_invocation_id
    assert lineage.root_run_id == original.execution.run_id
    assert lineage.tool_invocation_id == tool_invocation_id
    assert lineage.worker_job_id == bound.worker_job_id
    assert lineage.task_id == original.execution.subject_id
    assert bound.worker_job_id.startswith("worker_job_")


def test_tool_worker_job_identity_is_idempotent_per_tool_call_and_distinct_between_calls() -> None:
    original = _job()
    first_tool = new_id("tool_invocation")
    second_tool = new_id("tool_invocation")

    first = bind_worker_job_to_tool_invocation(original, first_tool)
    first_retry = bind_worker_job_to_tool_invocation(original, first_tool)
    second = bind_worker_job_to_tool_invocation(original, second_tool)

    assert first.worker_job_id == first_retry.worker_job_id
    assert first.worker_job_id == worker_job_id_for_tool_invocation(
        original.execution.run_id,
        first_tool,
    )
    assert second.worker_job_id != first.worker_job_id
    assert first.execution.run_id == second.execution.run_id == original.execution.run_id


def test_worker_transport_round_trip_preserves_tool_subexecution_lineage() -> None:
    bound = bind_worker_job_to_tool_invocation(_job(), new_id("tool_invocation"))

    decoded = WorkerTransportCodec.decode_job(WorkerTransportCodec.encode_job(bound))

    assert decoded == bound
    assert tool_lineage(decoded) == tool_lineage(bound)


def test_tool_lineage_rejects_noncanonical_or_mismatched_bindings() -> None:
    original = _job()
    with pytest.raises(ValueError, match="canonical tool_invocation"):
        bind_worker_job_to_tool_invocation(original, "tool-call-provider-private")

    bound = bind_worker_job_to_tool_invocation(original, new_id("tool_invocation"))
    mismatched = replace(bound, worker_job_id=new_id("worker_job"))
    with pytest.raises(ValueError, match="does not match"):
        tool_lineage(mismatched)
