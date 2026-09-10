from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.contracts import ContractError, DataClassification, ErrorCode
from ai_multi_agent_platform.data.models import FileRecord, FileState
from ai_multi_agent_platform.domain import OwnerRef, Run, RunStatus, Task, TaskStatus, new_id
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.verification.models import (
    VerificationRequest,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.reviewer_input import (
    KernelFileReviewerSubjectInputProvider,
)


class _Tasks:
    def __init__(self, state: TaskState) -> None:
        self.state = state

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.state.task_id
        return self.state


class _Runs:
    def __init__(self, state: RunState) -> None:
        self.state = state

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        assert task_id == self.state.task_id
        assert run_id == self.state.run_id
        return self.state


class _Files:
    def __init__(self, record: FileRecord | None = None, payload: bytes = b"") -> None:
        self.record = record
        self.payload = payload
        self.last_actor_ref: str | None = None

    async def get_file(self, file_id: str, context) -> FileRecord:
        assert self.record is not None
        assert file_id == self.record.file_id
        self.last_actor_ref = context.actor_ref
        return self.record

    async def verify_checksum(self, file_id: str, context) -> bool:
        assert self.record is not None
        assert file_id == self.record.file_id
        self.last_actor_ref = context.actor_ref
        return hashlib.sha256(self.payload).hexdigest() == self.record.sha256

    async def stream_file(self, file_id: str, context, *, chunk_size: int = 64 * 1024):
        assert self.record is not None
        assert file_id == self.record.file_id
        self.last_actor_ref = context.actor_ref
        del chunk_size
        yield self.payload


def _states(*, output=None, result_ids=(), artifact_ids=()):
    owner = OwnerRef(type="user", id="issue-711-reviewer-input")
    task_id = new_id("task")
    run_id = new_id("run")
    task = Task(
        id=task_id,
        title="Review exact output",
        description="Produce and review an exact canonical subject.",
        owner_ref=owner,
        status=TaskStatus.RUNNING,
    )
    run = Run(
        id=run_id,
        subject_type="task",
        subject_id=task_id,
        owner_ref=owner,
        correlation_id=task_id,
        status=RunStatus.SUCCEEDED,
        attempt=1,
    )
    task_state = TaskState(
        task=task,
        revision=4,
        run_ids=(run_id,),
        result_ids=result_ids,
        artifact_ids=artifact_ids,
    )
    run_state = RunState(
        run=run,
        revision=3,
        output=output or {},
        result_ids=result_ids,
        artifact_ids=artifact_ids,
    )
    return task_state, run_state


def _agent_run(task_id: str, run_id: str) -> AgentRunRecord:
    return AgentRunRecord(
        agent_run_id=new_id("agent_run"),
        run_id=run_id,
        task_id=task_id,
        agent=AgentRevisionRef(new_id("agent"), 1),
        status=AgentRunStatus.RUNNING,
    )


def _request(
    task_id: str,
    run_id: str,
    subject: VerificationSubject,
) -> VerificationRequest:
    return VerificationRequest(
        task_id=task_id,
        policy_id=new_id("verification_policy"),
        policy_version=1,
        stage_id="review",
        subject=subject,
        requested_verifier_kind=VerifierKind.AGENT,
        correlation_id=task_id,
        run_id=run_id,
        result_id=subject.subject_id if subject.subject_type == "result" else None,
        artifact_ids=(subject.subject_id,) if subject.subject_type == "artifact" else (),
    )


def _result_digest(task_id: str, result_id: str, run: RunState) -> str:
    snapshot = {
        "type": "result",
        "id": result_id,
        "task_id": task_id,
        "run_id": run.run_id,
        "run_attempt": run.attempt,
        "run_status": run.status.value,
        "output": run.output,
        "artifact_ids": list(run.artifact_ids),
    }
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def test_result_input_uses_exact_bound_run_output_and_conservative_classification() -> None:
    async def scenario() -> None:
        result_id = new_id("result")
        task, run = _states(output={"answer": 42}, result_ids=(result_id,))
        subject = VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision=f"{run.run_id}:attempt:{run.attempt}",
            digest=_result_digest(task.task_id, result_id, run),
        )
        provider = KernelFileReviewerSubjectInputProvider(
            tasks=_Tasks(task),
            runs=_Runs(run),
            files=_Files(),  # type: ignore[arg-type]
        )

        loaded = await provider.load(
            request=_request(task.task_id, run.run_id, subject),
            agent_run=_agent_run(task.task_id, run.run_id),
        )

        assert loaded.subject == subject
        assert loaded.classification is DataClassification.RESTRICTED
        assert '"answer":42' in loaded.content
        assert f'"run_id":"{run.run_id}"' in loaded.content
        assert loaded.evidence_artifact_ids == ()

    asyncio.run(scenario())


def test_result_input_rejects_stale_run_revision() -> None:
    async def scenario() -> None:
        result_id = new_id("result")
        task, run = _states(output={"answer": 42}, result_ids=(result_id,))
        subject = VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="stale-run-attempt",
            digest=_result_digest(task.task_id, result_id, run),
        )
        provider = KernelFileReviewerSubjectInputProvider(
            tasks=_Tasks(task),
            runs=_Runs(run),
            files=_Files(),  # type: ignore[arg-type]
        )

        with pytest.raises(ContractError) as exc_info:
            await provider.load(
                request=_request(task.task_id, run.run_id, subject),
                agent_run=_agent_run(task.task_id, run.run_id),
            )
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())


def test_result_input_rejects_snapshot_changed_after_verification_binding() -> None:
    async def scenario() -> None:
        result_id = new_id("result")
        task, original = _states(output={"answer": 42}, result_ids=(result_id,))
        subject = VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision=f"{original.run_id}:attempt:{original.attempt}",
            digest=_result_digest(task.task_id, result_id, original),
        )
        changed = RunState(
            run=original.run,
            revision=original.revision + 1,
            output=original.output,
            result_ids=original.result_ids,
            artifact_ids=(new_id("artifact"),),
        )
        provider = KernelFileReviewerSubjectInputProvider(
            tasks=_Tasks(task),
            runs=_Runs(changed),
            files=_Files(),  # type: ignore[arg-type]
        )

        with pytest.raises(ContractError) as exc_info:
            await provider.load(
                request=_request(task.task_id, original.run_id, subject),
                agent_run=_agent_run(task.task_id, original.run_id),
            )
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
        assert "digest differs" in str(exc_info.value)

    asyncio.run(scenario())


def test_artifact_input_reads_exact_file_revision_and_preserves_classification() -> None:
    async def scenario() -> None:
        artifact_id = new_id("artifact")
        payload = b"review this exact artifact\n"
        digest = hashlib.sha256(payload).hexdigest()
        file_id = new_id("file")
        task, run = _states(artifact_ids=(artifact_id,))
        record = FileRecord(
            file_id=file_id,
            project_id=None,
            owner_ref="user:issue-711-reviewer-input",
            created_by="service:test",
            created_at=datetime.now(UTC),
            size_bytes=len(payload),
            sha256=digest,
            state=FileState.READY,
            content_type="text/plain",
            artifact_ids=(artifact_id,),
            classification=DataClassification.CONFIDENTIAL,
        )
        files = _Files(record, payload)
        provider = KernelFileReviewerSubjectInputProvider(
            tasks=_Tasks(task),
            runs=_Runs(run),
            files=files,  # type: ignore[arg-type]
        )
        subject = VerificationSubject(
            subject_type="artifact",
            subject_id=artifact_id,
            revision=file_id,
            digest=f"sha256:{digest}",
        )
        reviewer = _agent_run(task.task_id, run.run_id)

        loaded = await provider.load(
            request=_request(task.task_id, run.run_id, subject),
            agent_run=reviewer,
        )

        assert loaded.subject == subject
        assert loaded.classification is DataClassification.CONFIDENTIAL
        assert loaded.evidence_artifact_ids == (artifact_id,)
        assert "review this exact artifact" in loaded.content
        assert files.last_actor_ref == f"agent:{reviewer.agent.agent_id}@1"

    asyncio.run(scenario())


def test_artifact_input_rejects_changed_digest_before_model_execution() -> None:
    async def scenario() -> None:
        artifact_id = new_id("artifact")
        payload = b"immutable artifact"
        digest = hashlib.sha256(payload).hexdigest()
        file_id = new_id("file")
        task, run = _states(artifact_ids=(artifact_id,))
        record = FileRecord(
            file_id=file_id,
            project_id=None,
            owner_ref="user:issue-711-reviewer-input",
            created_by="service:test",
            created_at=datetime.now(UTC),
            size_bytes=len(payload),
            sha256=digest,
            state=FileState.READY,
            content_type="text/plain",
            artifact_ids=(artifact_id,),
        )
        provider = KernelFileReviewerSubjectInputProvider(
            tasks=_Tasks(task),
            runs=_Runs(run),
            files=_Files(record, payload),  # type: ignore[arg-type]
        )
        subject = VerificationSubject(
            subject_type="artifact",
            subject_id=artifact_id,
            revision=file_id,
            digest="sha256:changed",
        )

        with pytest.raises(ContractError) as exc_info:
            await provider.load(
                request=_request(task.task_id, run.run_id, subject),
                agent_run=_agent_run(task.task_id, run.run_id),
            )
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())
