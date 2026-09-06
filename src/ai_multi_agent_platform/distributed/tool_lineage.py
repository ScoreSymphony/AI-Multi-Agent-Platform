"""Canonical lineage helpers for tool-triggered Worker subexecutions.

A tool/capability call is not a second canonical Run. The parent ``ExecutionRequest.run_id``
continues to identify the one canonical Run attempt, while ``worker_job_id`` identifies the
concrete Worker subexecution. A canonical ``tool_invocation_*`` is carried as the direct
causation link so tracing can follow Run -> ToolInvocation -> WorkerJob without inventing a
synthetic Run identity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.domain import validate_id

from .models import WorkerJobRequest

_TOOL_WORKER_JOB_NAMESPACE = "ai-multi-agent-platform:tool-worker-job"


@dataclass(frozen=True, slots=True)
class WorkerToolLineage:
    """Normalized canonical identity links for one tool-triggered Worker job."""

    worker_job_id: str
    root_run_id: str
    tool_invocation_id: str
    correlation_id: str
    task_id: str | None

    def __post_init__(self) -> None:
        validate_id(self.worker_job_id, "worker_job")
        validate_id(self.root_run_id, "run")
        validate_id(self.tool_invocation_id, "tool_invocation")
        if not self.correlation_id.strip():
            raise ValueError("correlation_id must not be blank")
        if self.task_id is not None:
            validate_id(self.task_id, "task")


def worker_job_id_for_tool_invocation(run_id: str, tool_invocation_id: str) -> str:
    """Derive the stable WorkerJob identity for one canonical tool invocation.

    The derivation is idempotent for retries of the same tool invocation and distinct for
    separate tool invocations within the same Run. It never derives or changes the Run ID.
    """

    validate_id(run_id, "run")
    validate_id(tool_invocation_id, "tool_invocation")
    value = uuid5(NAMESPACE_URL, f"{_TOOL_WORKER_JOB_NAMESPACE}:{run_id}:{tool_invocation_id}")
    return f"worker_job_{value}"


def bind_worker_job_to_tool_invocation(
    job: WorkerJobRequest,
    tool_invocation_id: str,
) -> WorkerJobRequest:
    """Return a WorkerJob bound to a canonical tool invocation without changing its Run.

    ``OperationContext.causation_id`` is the existing cross-provider causation seam and is
    already preserved by Worker transport and persistence. The returned job therefore carries
    the direct ToolInvocation cause while retaining the original correlation, task/step subject,
    project, ownership and control context.
    """

    validate_id(tool_invocation_id, "tool_invocation")
    execution = job.execution
    context = replace(execution.context, causation_id=tool_invocation_id)
    return replace(
        job,
        worker_job_id=worker_job_id_for_tool_invocation(execution.run_id, tool_invocation_id),
        execution=replace(execution, context=context),
    )


def tool_lineage(job: WorkerJobRequest) -> WorkerToolLineage:
    """Read and validate the canonical tool lineage carried by a WorkerJobRequest."""

    tool_invocation_id = job.execution.context.causation_id
    if tool_invocation_id is None:
        raise ValueError("worker job is not bound to a canonical tool invocation")
    validate_id(tool_invocation_id, "tool_invocation")
    expected_worker_job_id = worker_job_id_for_tool_invocation(
        job.execution.run_id,
        tool_invocation_id,
    )
    if job.worker_job_id != expected_worker_job_id:
        raise ValueError("worker job identity does not match its Run/ToolInvocation lineage")
    task_id = job.execution.subject_id if job.execution.subject_type == "task" else None
    return WorkerToolLineage(
        worker_job_id=job.worker_job_id,
        root_run_id=job.execution.run_id,
        tool_invocation_id=tool_invocation_id,
        correlation_id=job.execution.context.correlation_id,
        task_id=task_id,
    )


__all__ = [
    "WorkerToolLineage",
    "bind_worker_job_to_tool_invocation",
    "tool_lineage",
    "worker_job_id_for_tool_invocation",
]
