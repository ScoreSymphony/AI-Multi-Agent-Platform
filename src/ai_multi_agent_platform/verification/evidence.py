"""Canonical Result/Artifact evidence resolution for runtime Verification (#86)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileState
from ai_multi_agent_platform.domain import RunStatus, validate_id

from .deterministic import ReferenceDeterministicVerifier
from .gate import TaskVerificationRequirement, VerificationCompletionAuthority
from .models import (
    ProducerIdentity,
    VerificationRequest,
    VerificationResult,
    VerificationSubject,
)
from .service import _CANONICAL_RESULT_TOKEN

if TYPE_CHECKING:
    from ai_multi_agent_platform.agents import AgentRepository
    from ai_multi_agent_platform.kernel import PlatformKernel
    from ai_multi_agent_platform.kernel.repository import EventRepository


@dataclass(frozen=True, slots=True)
class VerificationEvidenceContext:
    """Canonical subject plus producer/scope facts derived from platform state."""

    task_id: str
    subject: VerificationSubject
    run_id: str | None
    project_id: str | None
    capability_ids: tuple[str, ...]
    producer: ProducerIdentity | None


@runtime_checkable
class VerificationEvidenceResolver(Protocol):
    """Resolve exact verification subjects/evidence from canonical platform state."""

    async def resolve_subject(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationSubject: ...

    async def resolve_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationEvidenceContext: ...

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]: ...


class KernelFileVerificationEvidenceResolver:
    """Reference resolver over canonical kernel history and the replaceable FileProvider.

    Result subjects are bound to one terminal Run's immutable output projection. File-backed
    Artifact subjects are bound to their verified canonical FileRecord SHA-256. A different
    Artifact backend can replace this resolver without changing Verification semantics.
    """

    def __init__(
        self,
        kernel: PlatformKernel,
        events: EventRepository,
        files: FileProvider,
        agents: AgentRepository | None = None,
    ) -> None:
        self._kernel = kernel
        self._events = events
        self._files = files
        self._agents = agents

    async def resolve_subject(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationSubject:
        validate_id(task_id, "task")
        if subject_type == "result":
            validate_id(subject_id, "result")
            return await self._resolve_result(task_id, subject_id)
        if subject_type == "artifact":
            validate_id(subject_id, "artifact")
            return await self._resolve_artifact(task_id, subject_id)
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "verification subject_type must be result or artifact",
        )

    async def resolve_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationEvidenceContext:
        task = await self._kernel.get_task(task_id)
        subject = await self.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        run_id: str | None = None
        if subject_type == "result":
            attachments = [
                event
                for event in await self._events.read_events(task_id)
                if event.event_type == "result.attached"
                and event.payload.get("result_id") == subject_id
            ]
            if not attachments or attachments[-1].subject_type != "run":
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "canonical Result producer context requires a Run attachment",
                )
            run_id = attachments[-1].subject_id
        producer, capability_ids, producer_run_id = self._producer_context(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
            run_id=run_id,
        )
        if run_id is None:
            run_id = producer_run_id
        elif producer_run_id is not None and producer_run_id != run_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical producer AgentRun belongs to a different Run",
            )
        return VerificationEvidenceContext(
            task_id=task_id,
            subject=subject,
            run_id=run_id,
            project_id=task.task.project_id,
            capability_ids=capability_ids,
            producer=producer,
        )

    def _producer_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
        run_id: str | None,
    ) -> tuple[ProducerIdentity | None, tuple[str, ...], str | None]:
        if self._agents is None:
            return None, (), run_id
        records = (
            self._agents.list_agent_runs(run_id)
            if run_id is not None
            else self._agents.list_agent_runs()
        )
        candidates = [
            record
            for record in records
            if record.task_id == task_id
            and (
                subject_id in record.result_ids
                if subject_type == "result"
                else subject_id in record.artifact_ids
            )
        ]
        if len(candidates) > 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification subject maps to multiple canonical producer AgentRuns",
            )
        if not candidates:
            return None, (), run_id
        record = candidates[0]
        return (
            ProducerIdentity(
                actor_ref=f"agent:{record.agent.agent_id}@{record.agent.revision}",
                agent_id=record.agent.agent_id,
                agent_revision=record.agent.revision,
                model_config_id=record.selected_model_config_id,
                provider_id=record.selected_provider_id,
            ),
            record.capability_ids,
            record.run_id,
        )

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "verification evidence IDs must be unique"
            )
        for artifact_id in artifact_ids:
            await self._resolve_artifact(task_id, artifact_id)
        return artifact_ids

    async def _resolve_result(self, task_id: str, result_id: str) -> VerificationSubject:
        task = await self._kernel.get_task(task_id)
        if result_id not in task.result_ids:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"result is not attached to task: {result_id}",
            )
        attachments = [
            event
            for event in await self._events.read_events(task_id)
            if event.event_type == "result.attached" and event.payload.get("result_id") == result_id
        ]
        if not attachments:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical task state references a result without an attachment event",
            )
        attachment = attachments[-1]
        if attachment.subject_type != "run":
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "exact Result verification requires a Result attached to a canonical Run",
            )
        run = await self._kernel.get_run(task_id, attachment.subject_id)
        if result_id not in run.result_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "result attachment is not present in the canonical Run projection",
            )
        if run.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }:
            raise ContractError(
                ErrorCode.CONFLICT,
                "verification cannot bind to a non-terminal Run result",
            )
        snapshot = {
            "type": "result",
            "id": result_id,
            "task_id": task_id,
            "run_id": run.run_id,
            "run_attempt": run.attempt,
            "run_status": run.status.value,
            "output": _plain_json(run.output),
            "artifact_ids": list(run.artifact_ids),
        }
        return VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision=f"{run.run_id}:attempt:{run.attempt}",
            digest=_digest(snapshot),
        )

    async def _resolve_artifact(self, task_id: str, artifact_id: str) -> VerificationSubject:
        task = await self._kernel.get_task(task_id)
        if artifact_id not in task.artifact_ids:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"artifact is not attached to task: {artifact_id}",
            )
        context = _file_context(
            task_id, task.task.owner_ref.type, task.task.owner_ref.id, task.task.project_id
        )
        linked = [
            record
            for record in await self._files.list_files(context)
            if artifact_id in record.artifact_ids
        ]
        if not linked:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "artifact verification requires canonical file-backed evidence",
            )
        if len(linked) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "artifact resolves to multiple canonical files",
            )
        record = linked[0]
        if record.state is not FileState.READY:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "artifact file is not in canonical ready state",
            )
        if not await self._files.verify_checksum(record.file_id, context):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "artifact file checksum does not match canonical metadata",
            )
        return VerificationSubject(
            subject_type="artifact",
            subject_id=artifact_id,
            revision=record.file_id,
            digest=f"sha256:{record.sha256}",
        )


class CanonicalVerificationRuntime:
    """High-level request path that derives exact subjects from canonical state."""

    def __init__(
        self,
        completion: VerificationCompletionAuthority,
        evidence: VerificationEvidenceResolver,
    ) -> None:
        self._completion = completion
        self._evidence = evidence

    @property
    def evidence(self) -> VerificationEvidenceResolver:
        return self._evidence

    def require_task(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
    ) -> TaskVerificationRequirement:
        return self._completion.require_task(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
        )

    async def request_verification(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        subject_type: str,
        subject_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        context = await self._evidence.resolve_context(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        result_id = subject_id if subject_type == "result" else None
        artifact_ids = (subject_id,) if subject_type == "artifact" else ()
        return self._completion.request_canonical_verification(
            task_id=context.task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=context.subject,
            correlation_id=correlation_id,
            run_id=context.run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=context.project_id,
            capability_ids=context.capability_ids,
            producer=context.producer,
            causation_id=causation_id,
        )

    async def submit_result(self, result: VerificationResult) -> VerificationResult:
        request = self._completion.verification.get_request(result.verification_id)
        canonical_subject = await self._evidence.resolve_subject(
            task_id=request.task_id,
            subject_type=request.subject.subject_type,
            subject_id=request.subject.subject_id,
        )
        if canonical_subject != request.subject or result.subject != canonical_subject:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification result subject differs from current canonical evidence",
            )
        await self._evidence.validate_evidence_artifacts(
            task_id=request.task_id,
            artifact_ids=result.evidence_artifact_ids,
        )
        return self._completion.verification.submit_result(
            result, _canonical_result_token=_CANONICAL_RESULT_TOKEN
        )

    async def run_deterministic(
        self,
        verification_id: str,
        verifier: ReferenceDeterministicVerifier,
    ) -> VerificationResult:
        request = self._completion.verification.get_request(verification_id)
        return await self.submit_result(verifier.verify(request))

    async def request_reverification_after_repair(
        self,
        verification_id: str,
        *,
        subject_type: str,
        subject_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        previous = self._completion.verification.get_request(verification_id)
        context = await self._evidence.resolve_context(
            task_id=previous.task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        result_id = subject_id if subject_type == "result" else None
        artifact_ids = (subject_id,) if subject_type == "artifact" else ()
        return self._completion.request_canonical_reverification_after_repair(
            verification_id,
            new_subject=context.subject,
            correlation_id=correlation_id,
            run_id=context.run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=context.project_id,
            capability_ids=context.capability_ids,
            producer=context.producer,
            causation_id=causation_id,
        )


def _file_context(
    task_id: str,
    owner_type: str,
    owner_id: str,
    project_id: str | None,
) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=task_id,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
        ),
        actor_ref="service:verification",
        task_id=task_id,
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
