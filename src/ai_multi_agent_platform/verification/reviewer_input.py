"""Canonical Result/Artifact input loading for automatic reviewer Agents (#711).

This module supplies the concrete local-first input boundary for ``ModelRuntimeReviewerExecutor``.
It resolves only the exact immutable Verification subject through canonical kernel/file state and
never lets reviewer/model output choose its own evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import cast

from ai_multi_agent_platform.agents import AgentRunRecord
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileState
from ai_multi_agent_platform.kernel.models import RunState
from ai_multi_agent_platform.kernel.repository import RunRepository, TaskRepository

from .models import VerificationRequest, VerificationSubject
from .reference_reviewer import ReviewerSubjectInput, ReviewerSubjectInputProvider

_DEFAULT_MAX_REVIEW_INPUT_BYTES = 256 * 1024


class KernelFileReviewerSubjectInputProvider(ReviewerSubjectInputProvider):
    """Load exact Result/Artifact content through canonical kernel and file boundaries.

    Result input comes from the producer Run pinned on the VerificationRequest. Artifact input
    is resolved from the canonical file revision stored on VerificationSubject and read only
    through FileProvider. Inputs are bounded before entering the model context.
    """

    def __init__(
        self,
        *,
        tasks: TaskRepository,
        runs: RunRepository,
        files: FileProvider,
        max_input_bytes: int = _DEFAULT_MAX_REVIEW_INPUT_BYTES,
        result_classification: DataClassification = DataClassification.RESTRICTED,
    ) -> None:
        if max_input_bytes < 1:
            raise ValueError("max_input_bytes must be >= 1")
        self._tasks = tasks
        self._runs = runs
        self._files = files
        self._max_input_bytes = max_input_bytes
        self._result_classification = result_classification

    async def load(
        self,
        *,
        request: VerificationRequest,
        agent_run: AgentRunRecord,
    ) -> ReviewerSubjectInput:
        if agent_run.task_id != request.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewer AgentRun belongs to a different Task",
            )
        if request.subject.subject_type == "result":
            return await self._load_result(request)
        return await self._load_artifact(request, agent_run)

    async def _load_result(self, request: VerificationRequest) -> ReviewerSubjectInput:
        if request.run_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Result reviewer input requires the canonical producer Run binding",
            )
        run = await self._runs.get_run(request.task_id, request.run_id)
        if request.subject.subject_id not in run.result_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Result is not attached to the bound canonical Run",
            )
        expected_revision = f"{run.run_id}:attempt:{run.attempt}"
        if request.subject.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Result revision differs from the bound canonical Run attempt",
            )

        snapshot = _result_snapshot(
            task_id=request.task_id,
            result_id=request.subject.subject_id,
            run=run,
        )
        if request.subject.digest != _digest(snapshot):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Result digest differs from current canonical Run evidence",
            )

        # Feed the model only fields covered by the canonical Result digest. In particular,
        # do not include mutable Run fields that are not part of the #86 subject snapshot.
        content = _canonical_json(
            {
                "subject": _subject_payload(request.subject),
                "result": snapshot,
            }
        )
        _require_bounded(content, self._max_input_bytes, subject_type="Result")
        return ReviewerSubjectInput(
            subject=request.subject,
            content=content,
            classification=self._result_classification,
        )

    async def _load_artifact(
        self,
        request: VerificationRequest,
        agent_run: AgentRunRecord,
    ) -> ReviewerSubjectInput:
        task = await self._tasks.get_task(request.task_id)
        operation = OperationContext(
            correlation_id=request.correlation_id,
            causation_id=request.verification_id,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
        )
        access = DataAccessContext(
            operation=operation,
            actor_ref=f"agent:{agent_run.agent.agent_id}@{agent_run.agent.revision}",
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=agent_run.agent.agent_id,
        )
        record = await self._files.get_file(request.subject.revision, access)
        if record.file_id != request.subject.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "file provider returned a different canonical Artifact revision",
            )
        if request.subject.subject_id not in record.artifact_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Artifact is not linked to the canonical file revision",
            )
        if record.state is not FileState.READY:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Artifact file is not in canonical ready state",
            )
        if request.subject.digest != f"sha256:{record.sha256}":
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Artifact digest differs from canonical file metadata",
            )
        if record.size_bytes > self._max_input_bytes:
            raise ContractError(
                ErrorCode.INPUT_TOO_LARGE,
                "reviewed Artifact exceeds the configured reviewer input limit",
                details={
                    "size_bytes": record.size_bytes,
                    "max_input_bytes": self._max_input_bytes,
                },
            )
        if not await self._files.verify_checksum(record.file_id, access):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewed Artifact checksum does not match canonical file metadata",
            )

        raw = bytearray()
        async for chunk in self._files.stream_file(record.file_id, access):
            raw.extend(chunk)
            if len(raw) > self._max_input_bytes:
                raise ContractError(
                    ErrorCode.INPUT_TOO_LARGE,
                    "reviewed Artifact stream exceeds the configured reviewer input limit",
                )
        try:
            body = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "reference reviewer currently requires UTF-8 textual Artifact evidence",
            ) from exc

        content = _canonical_json(
            {
                "subject": _subject_payload(request.subject),
                "file": {
                    "file_id": record.file_id,
                    "sha256": record.sha256,
                    "size_bytes": record.size_bytes,
                    "content_type": record.content_type,
                    "content": body,
                },
            }
        )
        _require_bounded(content, self._max_input_bytes, subject_type="Artifact")
        return ReviewerSubjectInput(
            subject=request.subject,
            content=content,
            evidence_artifact_ids=(request.subject.subject_id,),
            classification=_classification(record.classification),
        )


def _result_snapshot(*, task_id: str, result_id: str, run: RunState) -> dict[str, JsonValue]:
    """Mirror the canonical #86 Result subject snapshot used by the evidence resolver."""

    return {
        "type": "result",
        "id": result_id,
        "task_id": task_id,
        "run_id": run.run_id,
        "run_attempt": run.attempt,
        "run_status": run.status.value,
        "output": cast(JsonValue, _plain_json(run.output)),
        "artifact_ids": cast(JsonValue, list(run.artifact_ids)),
    }


def _subject_payload(subject: VerificationSubject) -> dict[str, JsonValue]:
    return {
        "type": subject.subject_type,
        "id": subject.subject_id,
        "revision": subject.revision,
        "digest": subject.digest,
    }


def _classification(value: DataClassification | str | None) -> DataClassification:
    if value is None:
        return DataClassification.INTERNAL
    if isinstance(value, DataClassification):
        return value
    try:
        return DataClassification(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "reviewed Artifact has an unknown data classification",
        ) from exc


def _require_bounded(content: str, limit: int, *, subject_type: str) -> None:
    size = len(content.encode("utf-8"))
    if size > limit:
        raise ContractError(
            ErrorCode.INPUT_TOO_LARGE,
            f"reviewed {subject_type} exceeds the configured reviewer input limit",
            details={"size_bytes": size, "max_input_bytes": limit},
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value: object) -> str:
    encoded = _canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = ["KernelFileReviewerSubjectInputProvider"]
