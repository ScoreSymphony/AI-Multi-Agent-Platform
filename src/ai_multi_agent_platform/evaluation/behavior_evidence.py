"""First-party non-output behavior evidence adapters for Evaluation."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distributed.runtime import DispatchRecord, DistributedRuntime
from ai_multi_agent_platform.security.approvals import ApprovalRecord

from .context import EvaluationExecutionContext
from .contracts import EvaluationCaseExecutor
from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation

_APPROVAL_KEY = "approval_behavior"
_DISTRIBUTED_KEY = "distributed_behavior"


class ApprovalRecordReader(Protocol):
    """Minimal approval read boundary consumed by Evaluation evidence projection."""

    def all(self) -> tuple[ApprovalRecord, ...]: ...


class DistributedRuntimeReader(Protocol):
    """Minimal distributed-runtime read boundary consumed by Evaluation."""

    def records(self) -> tuple[DispatchRecord, ...]: ...

    @property
    def registry(self) -> object: ...


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class ApprovalEvidenceCaseExecutor:
    """Project canonical approval requests/decisions for the evaluated Task/Run."""

    def __init__(self, executor: EvaluationCaseExecutor, approvals: ApprovalRecordReader) -> None:
        self._executor = executor
        self._approvals = approvals

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        observation = await self._executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=execution_context,
        )
        if _APPROVAL_KEY in observation.data:
            raise ValueError("evaluation observation data must not shadow approval_behavior evidence")
        records = tuple(
            record
            for record in self._approvals.all()
            if (observation.task_id is not None and record.task_id == observation.task_id)
            or (observation.run_id is not None and record.run_id == observation.run_id)
        )
        if not records:
            return observation

        payloads: list[JsonValue] = []
        for record in sorted(records, key=lambda item: item.approval_id):
            payloads.append(
                {
                    "approval_id": record.approval_id,
                    "status": record.status.value,
                    "action": record.action,
                    "resource_type": record.resource_type,
                    "resource_id": record.resource_id,
                    "risk": record.risk.value,
                    "policy_id": record.policy_id,
                    "task_id": record.task_id,
                    "run_id": record.run_id,
                    "capability_ref": record.capability_ref,
                    "payload_ref": record.payload_ref,
                    "requester_ref": record.requester_ref,
                    "reason": record.reason,
                    "decision_by": (
                        None
                        if record.decision_by is None
                        else f"{record.decision_by.type}:{record.decision_by.id}"
                    ),
                    "decision_comment": record.decision_comment,
                }
            )
        return replace(
            observation,
            data={**observation.data, _APPROVAL_KEY: {"records": payloads}},
        )


class DistributedRuntimeEvidenceCaseExecutor:
    """Project canonical worker-job placement for the evaluated Run.

    Selection evidence remains source-owned by the distributed runtime. Evaluation only
    projects records whose canonical execution ``run_id`` matches the wrapped result.
    """

    def __init__(self, executor: EvaluationCaseExecutor, runtime: DistributedRuntime) -> None:
        self._executor = executor
        self._runtime = runtime

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        observation = await self._executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=execution_context,
        )
        if observation.run_id is None:
            return observation
        if _DISTRIBUTED_KEY in observation.data:
            raise ValueError(
                "evaluation observation data must not shadow distributed_behavior evidence"
            )
        records = tuple(
            record
            for record in self._runtime.records()
            if record.job.execution.run_id == observation.run_id
        )
        if not records:
            return observation

        payloads: list[JsonValue] = []
        artifact_refs = list(observation.artifact_refs)
        for record in sorted(records, key=lambda item: item.job.worker_job_id):
            worker = self._runtime.registry.get_worker(record.worker_id)
            result = record.result
            if result is not None:
                artifact_refs.extend(result.artifact_refs)
            payloads.append(
                {
                    "worker_job_id": record.job.worker_job_id,
                    "worker_id": record.worker_id,
                    "node_id": worker.node_id,
                    "reservation_id": record.reservation_id,
                    "state": record.state.value,
                    "dispatch_attempt": record.job.dispatch_attempt,
                    "executor_type": record.job.requirements.executor_type,
                    "capability_refs": list(record.job.requirements.capability_refs),
                    "model_ref": record.job.requirements.model_ref,
                    "runtime": record.job.requirements.runtime,
                    "result_status": None if result is None else result.status.value,
                    "evidence_refs": [] if result is None else list(result.evidence_refs),
                }
            )
        return replace(
            observation,
            data={**observation.data, _DISTRIBUTED_KEY: {"jobs": payloads}},
            artifact_refs=_unique(tuple(artifact_refs)),
        )


__all__ = [
    "ApprovalEvidenceCaseExecutor",
    "ApprovalRecordReader",
    "DistributedRuntimeEvidenceCaseExecutor",
]
