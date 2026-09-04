from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# Policy hardening: explicit human/self independence and timeout outcome policy.
replace_once(
    "src/ai_multi_agent_platform/verification/models.py",
    '''class ReviewerIndependence:\n    """Policy-selectable independence constraints; none are mandatory globally."""\n\n    producer_agent_must_differ: bool = False\n    model_must_differ: bool = False\n    provider_must_differ: bool = False\n    agent_reviewer_must_be_read_only: bool = False\n    require_distinct_verifiers: bool = False\n''',
    '''class ReviewerIndependence:\n    """Policy-selectable independence constraints; none are mandatory globally."""\n\n    producer_agent_must_differ: bool = False\n    model_must_differ: bool = False\n    provider_must_differ: bool = False\n    agent_reviewer_must_be_read_only: bool = False\n    human_reviewer_must_differ: bool = False\n    forbid_self_verification: bool = False\n    require_distinct_verifiers: bool = False\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/models.py",
    '''    result_expiry_seconds: float | None = None\n    failure_policy: VerificationFailurePolicy = VerificationFailurePolicy.FAIL\n''',
    '''    result_expiry_seconds: float | None = None\n    failure_policy: VerificationFailurePolicy = VerificationFailurePolicy.FAIL\n    timeout_failure_policy: VerificationFailurePolicy = VerificationFailurePolicy.WAIT\n''',
)

# Verification request cancellation is a real lifecycle transition and audit fact.
replace_once(
    "src/ai_multi_agent_platform/verification/audit.py",
    '''    REQUEST_EXPIRED = "verification.request_expired"\n    RESULT_RECORDED = "verification.result_recorded"\n''',
    '''    REQUEST_EXPIRED = "verification.request_expired"\n    REQUEST_CANCELLED = "verification.request_cancelled"\n    RESULT_RECORDED = "verification.result_recorded"\n''',
)

replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    '''        return request\n\n    def result_for(self, verification_id: str) -> VerificationResult | None:\n''',
    '''        return request\n\n    def cancel_request(\n        self,\n        verification_id: str,\n        *,\n        now: datetime | None = None,\n        causation_id: str | None = None,\n    ) -> VerificationRequest:\n        """Cancel one still-pending canonical verification obligation."""\n\n        current = now or datetime.now(UTC)\n        if current.tzinfo is None or current.utcoffset() is None:\n            raise ValueError("verification cancellation time must be timezone-aware")\n        request = self.get_request(verification_id, now=current)\n        if request.status is VerificationRequestStatus.CANCELLED:\n            return request\n        if request.status is not VerificationRequestStatus.PENDING:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"verification request cannot be cancelled from {request.status.value}",\n            )\n        cancelled = replace(request, status=VerificationRequestStatus.CANCELLED)\n        self._requests[verification_id] = cancelled\n        self._audit_events.append(\n            VerificationAuditEvent(\n                event_type=VerificationAuditEventType.REQUEST_CANCELLED,\n                occurred_at=current,\n                task_id=cancelled.task_id,\n                verification_id=cancelled.verification_id,\n                run_id=cancelled.run_id,\n                project_id=cancelled.project_id,\n                policy_id=cancelled.policy_id,\n                policy_version=cancelled.policy_version,\n                stage_id=cancelled.stage_id,\n                subject=cancelled.subject,\n                requested_verifier_kind=cancelled.requested_verifier_kind,\n                repair_attempt=cancelled.repair_attempt,\n                correlation_id=cancelled.correlation_id,\n                causation_id=causation_id or cancelled.causation_id,\n            )\n        )\n        return cancelled\n\n    def result_for(self, verification_id: str) -> VerificationResult | None:\n''',
)

# Expired required requests use a policy-owned deterministic timeout decision.
replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    '''            if accepted_count < stage.minimum_results:\n                blocking.extend(request.verification_id for request, _result in matching)\n                return CompletionAssessment(\n                    task_id=task_id,\n                    subject=subject,\n                    state=CompletionState.WAITING,\n                    reason=f"verification stage {stage.stage_id!r} is incomplete",\n                    policy_id=policy_id,\n                    policy_version=policy_version,\n                    blocking_verification_ids=tuple(blocking),\n                    repair_attempts_remaining=max(\n                        0, policy.max_repair_attempts - max_repair_attempt\n                    ),\n                )\n''',
    '''            if accepted_count < stage.minimum_results:\n                stage_requests = self._matching_stage_requests(\n                    task_id=task_id,\n                    subject=subject,\n                    policy=policy,\n                    stage_id=stage.stage_id,\n                    now=current,\n                )\n                expired = [\n                    request\n                    for request in stage_requests\n                    if request.status is VerificationRequestStatus.EXPIRED\n                ]\n                if expired:\n                    return CompletionAssessment(\n                        task_id=task_id,\n                        subject=subject,\n                        state=self._failure_state_for(policy.timeout_failure_policy),\n                        reason=f"verification stage {stage.stage_id!r} timed out",\n                        policy_id=policy_id,\n                        policy_version=policy_version,\n                        blocking_verification_ids=tuple(\n                            request.verification_id for request in expired\n                        ),\n                        repair_attempts_remaining=max(\n                            0, policy.max_repair_attempts - max_repair_attempt\n                        ),\n                    )\n                blocking.extend(request.verification_id for request in stage_requests)\n                return CompletionAssessment(\n                    task_id=task_id,\n                    subject=subject,\n                    state=CompletionState.WAITING,\n                    reason=f"verification stage {stage.stage_id!r} is incomplete",\n                    policy_id=policy_id,\n                    policy_version=policy_version,\n                    blocking_verification_ids=tuple(blocking),\n                    repair_attempts_remaining=max(\n                        0, policy.max_repair_attempts - max_repair_attempt\n                    ),\n                )\n''',
)

replace_once(
    "src/ai_multi_agent_platform/verification/service.py",
    '''    def _matching_stage_results(\n        self,\n        *,\n        task_id: str,\n        subject: VerificationSubject,\n        policy: VerificationPolicy,\n        stage_id: str,\n        now: datetime,\n    ) -> list[tuple[VerificationRequest, VerificationResult]]:\n        matches: list[tuple[VerificationRequest, VerificationResult]] = []\n        for request in self._requests.values():\n            if (\n                request.task_id != task_id\n                or request.policy_id != policy.policy_id\n                or request.policy_version != policy.version\n                or request.stage_id != stage_id\n                or request.subject != subject\n            ):\n                continue\n            result = self.result_for(request.verification_id)\n            if result is None:\n                continue\n            if policy.result_expiry_seconds is not None:\n                expires_at = result.completed_at + timedelta(seconds=policy.result_expiry_seconds)\n                if expires_at <= now:\n                    continue\n            matches.append((request, result))\n        return matches\n\n    @staticmethod\n    def _failure_state(policy: VerificationPolicy) -> CompletionState:\n        if policy.failure_policy is VerificationFailurePolicy.WAIT:\n            return CompletionState.WAITING\n        if policy.failure_policy is VerificationFailurePolicy.ESCALATE:\n            return CompletionState.ESCALATED\n        return CompletionState.REJECTED\n''',
    '''    def _matching_stage_requests(\n        self,\n        *,\n        task_id: str,\n        subject: VerificationSubject,\n        policy: VerificationPolicy,\n        stage_id: str,\n        now: datetime,\n    ) -> list[VerificationRequest]:\n        matches: list[VerificationRequest] = []\n        for stored in list(self._requests.values()):\n            if (\n                stored.task_id != task_id\n                or stored.policy_id != policy.policy_id\n                or stored.policy_version != policy.version\n                or stored.stage_id != stage_id\n                or stored.subject != subject\n            ):\n                continue\n            matches.append(self.get_request(stored.verification_id, now=now))\n        return matches\n\n    def _matching_stage_results(\n        self,\n        *,\n        task_id: str,\n        subject: VerificationSubject,\n        policy: VerificationPolicy,\n        stage_id: str,\n        now: datetime,\n    ) -> list[tuple[VerificationRequest, VerificationResult]]:\n        matches: list[tuple[VerificationRequest, VerificationResult]] = []\n        for request in self._matching_stage_requests(\n            task_id=task_id,\n            subject=subject,\n            policy=policy,\n            stage_id=stage_id,\n            now=now,\n        ):\n            result = self.result_for(request.verification_id)\n            if result is None:\n                continue\n            if policy.result_expiry_seconds is not None:\n                expires_at = result.completed_at + timedelta(seconds=policy.result_expiry_seconds)\n                if expires_at <= now:\n                    continue\n            matches.append((request, result))\n        return matches\n\n    @staticmethod\n    def _failure_state_for(\n        failure_policy: VerificationFailurePolicy,\n    ) -> CompletionState:\n        if failure_policy is VerificationFailurePolicy.WAIT:\n            return CompletionState.WAITING\n        if failure_policy is VerificationFailurePolicy.ESCALATE:\n            return CompletionState.ESCALATED\n        return CompletionState.REJECTED\n\n    @classmethod\n    def _failure_state(cls, policy: VerificationPolicy) -> CompletionState:\n        return cls._failure_state_for(policy.failure_policy)\n''',
)

# Fail closed when a configured independence rule cannot be proven.
start = '''    @staticmethod\n    def _enforce_independence(\n        policy: VerificationPolicy,\n        request: VerificationRequest,\n        verifier: VerifierIdentity,\n    ) -> None:\n'''
end = '''    @staticmethod\n    def _require_policy_scope(\n'''
service_path = Path("src/ai_multi_agent_platform/verification/service.py")
service_text = service_path.read_text(encoding="utf-8")
if "verification policy cannot prove required producer identity" not in service_text:
    start_index = service_text.index(start)
    end_index = service_text.index(end, start_index)
    hardened = '''    @staticmethod\n    def _enforce_independence(\n        policy: VerificationPolicy,\n        request: VerificationRequest,\n        verifier: VerifierIdentity,\n    ) -> None:\n        rules = policy.independence\n        producer = request.producer\n\n        if verifier.kind is VerifierKind.AGENT and rules.agent_reviewer_must_be_read_only:\n            if not verifier.read_only:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy requires read-only reviewer-agent capabilities",\n                )\n\n        requires_producer = (\n            (rules.producer_agent_must_differ and verifier.kind is VerifierKind.AGENT)\n            or rules.model_must_differ\n            or rules.provider_must_differ\n            or (rules.human_reviewer_must_differ and verifier.kind is VerifierKind.HUMAN)\n            or rules.forbid_self_verification\n        )\n        if requires_producer and producer is None:\n            raise ContractError(\n                ErrorCode.FORBIDDEN,\n                "verification policy cannot prove required producer identity",\n            )\n        if producer is None:\n            return\n\n        if rules.producer_agent_must_differ and verifier.kind is VerifierKind.AGENT:\n            if producer.agent_id is None or verifier.agent_id is None:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy cannot prove producer/reviewer agent independence",\n                )\n            if verifier.agent_id == producer.agent_id:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy requires reviewer agent to differ from producer",\n                )\n\n        if rules.model_must_differ:\n            if producer.model_config_id is None or verifier.model_config_id is None:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy cannot prove reviewer model independence",\n                )\n            if verifier.model_config_id == producer.model_config_id:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy requires reviewer model to differ from producer model",\n                )\n\n        if rules.provider_must_differ:\n            if producer.provider_id is None or verifier.provider_id is None:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy cannot prove reviewer provider independence",\n                )\n            if verifier.provider_id == producer.provider_id:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy requires reviewer provider to differ from producer provider",\n                )\n\n        if rules.human_reviewer_must_differ and verifier.kind is VerifierKind.HUMAN:\n            if verifier.verifier_ref == producer.actor_ref:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy requires a separate human reviewer",\n                )\n\n        if rules.forbid_self_verification:\n            same_actor = verifier.verifier_ref == producer.actor_ref\n            same_agent = (\n                verifier.kind is VerifierKind.AGENT\n                and producer.agent_id is not None\n                and verifier.agent_id == producer.agent_id\n            )\n            if same_actor or same_agent:\n                raise ContractError(\n                    ErrorCode.FORBIDDEN,\n                    "verification policy forbids self-verification",\n                )\n\n'''
    service_path.write_text(
        service_text[:start_index] + hardened + service_text[end_index:], encoding="utf-8"
    )

# Durable cancellation persistence.
replace_once(
    "src/ai_multi_agent_platform/verification/persistence.py",
    '''    def submit_result(self, result: VerificationResult) -> VerificationResult:\n        submitted = super().submit_result(result)\n        self._save_service_state()\n        return submitted\n\n    def snapshot_requests(self) -> tuple[VerificationRequest, ...]:\n''',
    '''    def submit_result(self, result: VerificationResult) -> VerificationResult:\n        submitted = super().submit_result(result)\n        self._save_service_state()\n        return submitted\n\n    def cancel_request(\n        self,\n        verification_id: str,\n        *,\n        now: datetime | None = None,\n        causation_id: str | None = None,\n    ) -> VerificationRequest:\n        cancelled = super().cancel_request(\n            verification_id,\n            now=now,\n            causation_id=causation_id,\n        )\n        self._save_service_state()\n        return cancelled\n\n    def snapshot_requests(self) -> tuple[VerificationRequest, ...]:\n''',
)

# Canonical evidence resolver/runtime: callers provide IDs, never free-form digest/revision.
evidence_source = '''"""Canonical Result/Artifact evidence resolution for runtime Verification (#86)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileState
from ai_multi_agent_platform.domain import RunStatus, validate_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository

from .gate import VerificationCompletionAuthority
from .models import ProducerIdentity, VerificationRequest, VerificationSubject


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
    ) -> None:
        self._kernel = kernel
        self._events = events
        self._files = files

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

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ContractError(ErrorCode.INVALID_REQUEST, "verification evidence IDs must be unique")
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
        context = _file_context(task_id, task.task.owner_ref.type, task.task.owner_ref.id, task.task.project_id)
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
        run_id: str | None = None,
        project_id: str | None = None,
        capability_ids: tuple[str, ...] = (),
        producer: ProducerIdentity | None = None,
        repair_attempt: int = 0,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        subject = await self._evidence.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        result_id = subject_id if subject_type == "result" else None
        artifact_ids = (subject_id,) if subject_type == "artifact" else ()
        return self._completion.request_verification(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=project_id,
            capability_ids=capability_ids,
            producer=producer,
            repair_attempt=repair_attempt,
            causation_id=causation_id,
        )

    async def request_reverification_after_repair(
        self,
        verification_id: str,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
        correlation_id: str,
        run_id: str | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        subject = await self._evidence.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        result_id = subject_id if subject_type == "result" else None
        artifact_ids = (subject_id,) if subject_type == "artifact" else ()
        return self._completion.request_reverification_after_repair(
            verification_id,
            new_subject=subject,
            correlation_id=correlation_id,
            run_id=run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
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
'''
Path("src/ai_multi_agent_platform/verification/evidence.py").write_text(
    evidence_source, encoding="utf-8"
)

# Export only cycle-safe evidence contracts/runtime from verification package.
replace_once(
    "src/ai_multi_agent_platform/verification/__init__.py",
    '''from .deterministic import DeterministicCheck, ReferenceDeterministicVerifier\nfrom .gate import (\n''',
    '''from .deterministic import DeterministicCheck, ReferenceDeterministicVerifier\nfrom .evidence import (\n    CanonicalVerificationRuntime,\n    KernelFileVerificationEvidenceResolver,\n    VerificationEvidenceResolver,\n)\nfrom .gate import (\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/__init__.py",
    '''    "CompletionState",\n    "DeterministicCheck",\n''',
    '''    "CompletionState",\n    "CanonicalVerificationRuntime",\n    "DeterministicCheck",\n    "KernelFileVerificationEvidenceResolver",\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/__init__.py",
    '''    "VerificationError",\n    "VerificationFailurePolicy",\n''',
    '''    "VerificationError",\n    "VerificationEvidenceResolver",\n    "VerificationFailurePolicy",\n''',
)

# Human Control Plane review validates exact canonical subject and evidence when resolver exists.
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    '''from .gate import VerificationCompletionAuthority\nfrom .models import (\n''',
    '''from .evidence import VerificationEvidenceResolver\nfrom .gate import VerificationCompletionAuthority\nfrom .models import (\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    '''    def __init__(\n        self,\n        control_plane: ControlPlane,\n        verification: VerificationService,\n    ) -> None:\n        self._control_plane = control_plane\n        self._verification = verification\n\n    async def accept(\n''',
    '''    def __init__(\n        self,\n        control_plane: ControlPlane,\n        verification: VerificationService,\n        evidence: VerificationEvidenceResolver | None = None,\n    ) -> None:\n        self._control_plane = control_plane\n        self._verification = verification\n        self._evidence = evidence\n\n    async def accept(\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    '''        comment = _optional_comment(payload)\n        findings: tuple[VerificationFinding, ...] = ()\n''',
    '''        evidence_artifact_ids = _artifact_ids(payload)\n        if self._evidence is not None:\n            canonical_subject = await self._evidence.resolve_subject(\n                task_id=request.task_id,\n                subject_type=request.subject.subject_type,\n                subject_id=request.subject.subject_id,\n            )\n            if canonical_subject != request.subject:\n                raise ContractError(\n                    ErrorCode.CONTRACT_VIOLATION,\n                    "verification request subject differs from canonical Result/Artifact evidence",\n                )\n            await self._evidence.validate_evidence_artifacts(\n                task_id=request.task_id,\n                artifact_ids=evidence_artifact_ids,\n            )\n\n        comment = _optional_comment(payload)\n        findings: tuple[VerificationFinding, ...] = ()\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    '''                evidence_artifact_ids=_artifact_ids(payload),\n''',
    '''                evidence_artifact_ids=evidence_artifact_ids,\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    '''def register_verification_control_plane(\n    control_plane: ControlPlane,\n    verification: VerificationService,\n    completion: VerificationCompletionAuthority,\n) -> None:\n''',
    '''def register_verification_control_plane(\n    control_plane: ControlPlane,\n    verification: VerificationService,\n    completion: VerificationCompletionAuthority,\n    evidence: VerificationEvidenceResolver | None = None,\n) -> None:\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/control_plane.py",
    '''    handlers = VerificationCommandHandlers(control_plane, verification)\n''',
    '''    handlers = VerificationCommandHandlers(control_plane, verification, evidence)\n''',
)

# Reviewer Agents also validate the exact canonical subject before execution when configured.
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    '''from .models import (\n''',
    '''from .evidence import VerificationEvidenceResolver\nfrom .models import (\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    '''    def __init__(self, verification: VerificationService, agents: AgentRuntime) -> None:\n        self._verification = verification\n        self._agents = agents\n''',
    '''    def __init__(\n        self,\n        verification: VerificationService,\n        agents: AgentRuntime,\n        evidence: VerificationEvidenceResolver | None = None,\n    ) -> None:\n        self._verification = verification\n        self._agents = agents\n        self._evidence = evidence\n''',
)
replace_once(
    "src/ai_multi_agent_platform/verification/reviewer_agent.py",
    '''        if request.requested_verifier_kind is not VerifierKind.AGENT:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                "verification request is not assigned to a reviewer Agent",\n            )\n        if task_context is not None and _RESERVED_TASK_CONTEXT_KEY in task_context:\n''',
    '''        if request.requested_verifier_kind is not VerifierKind.AGENT:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                "verification request is not assigned to a reviewer Agent",\n            )\n        if self._evidence is not None:\n            canonical_subject = await self._evidence.resolve_subject(\n                task_id=request.task_id,\n                subject_type=request.subject.subject_type,\n                subject_id=request.subject.subject_id,\n            )\n            if canonical_subject != request.subject:\n                raise ContractError(\n                    ErrorCode.CONTRACT_VIOLATION,\n                    "reviewer Agent request subject differs from canonical evidence",\n                )\n        if task_context is not None and _RESERVED_TASK_CONTEXT_KEY in task_context:\n''',
)

# Production-shaped single-node composition now actually enables durable Verification.
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    '''from ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider\n''',
    '''from ai_multi_agent_platform.verification import (\n    CanonicalVerificationRuntime,\n    KernelFileVerificationEvidenceResolver,\n    SqliteVerificationCompletionAuthority,\n    SqliteVerificationService,\n)\nfrom ai_multi_agent_platform.verification.control_plane import register_verification_control_plane\nfrom ai_multi_agent_platform.verification.observability import VerificationTimelineReader\nfrom ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider\n''',
)
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    '''    authorization: SqliteLocalAuthorizationProvider\n    kernel: PlatformKernel\n    control_plane: ControlPlane\n''',
    '''    authorization: SqliteLocalAuthorizationProvider\n    verification: SqliteVerificationService\n    verification_completion: SqliteVerificationCompletionAuthority\n    verification_runtime: CanonicalVerificationRuntime\n    kernel: PlatformKernel\n    control_plane: ControlPlane\n''',
)
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    '''    orchestrator = ReferenceOrchestrator()\n    lifecycle = ExecutorLifecycleBackend(\n        ReferenceExecutor(config.executor_dir),\n        workspace=_REFERENCE_EXECUTION_WORKSPACE,\n        action="echo",\n    )\n    kernel = PlatformKernel(\n        orchestrator=orchestrator,\n        lifecycle=lifecycle,\n        repository=kernel_repository,\n    )\n''',
    '''    orchestrator = ReferenceOrchestrator()\n    lifecycle = ExecutorLifecycleBackend(\n        ReferenceExecutor(config.executor_dir),\n        workspace=_REFERENCE_EXECUTION_WORKSPACE,\n        action="echo",\n    )\n    verification_path = database_dir / "verification.sqlite3"\n    verification = SqliteVerificationService(verification_path)\n    verification_completion = SqliteVerificationCompletionAuthority(\n        verification, verification_path\n    )\n    kernel = PlatformKernel(\n        orchestrator=orchestrator,\n        lifecycle=lifecycle,\n        repository=kernel_repository,\n        completion_authority=verification_completion,\n    )\n    verification_evidence = KernelFileVerificationEvidenceResolver(\n        kernel, kernel_repository, files\n    )\n    verification_runtime = CanonicalVerificationRuntime(\n        verification_completion, verification_evidence\n    )\n''',
)
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    '''    register_agent_control_plane(control_plane, agents)\n    register_standard_agent_control_plane(control_plane, agents)\n\n    http = AuthenticatedControlPlaneHTTP(\n''',
    '''    register_agent_control_plane(control_plane, agents)\n    register_standard_agent_control_plane(control_plane, agents)\n    register_verification_control_plane(\n        control_plane,\n        verification,\n        verification_completion,\n        verification_evidence,\n    )\n    control_plane.bind_observability_timeline(VerificationTimelineReader(verification))\n\n    http = AuthenticatedControlPlaneHTTP(\n''',
)
replace_once(
    "src/ai_multi_agent_platform/deployment/single_node.py",
    '''        authorization=authorization,\n        kernel=kernel,\n        control_plane=control_plane,\n''',
    '''        authorization=authorization,\n        verification=verification,\n        verification_completion=verification_completion,\n        verification_runtime=verification_runtime,\n        kernel=kernel,\n        control_plane=control_plane,\n''',
)

# Focused regression coverage for the previously missing completion details.
test_source = '''from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionStatus, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CompletionState,
    KernelFileVerificationEvidenceResolver,
    ProducerIdentity,
    ReviewerIndependence,
    VerificationFailurePolicy,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequestStatus,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from ai_multi_agent_platform.verification.audit import VerificationAuditEventType


def _subject() -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:test",
    )


def _request(
    service: VerificationService,
    policy: VerificationPolicy,
    *,
    producer: ProducerIdentity | None = None,
) -> tuple[str, VerificationSubject, str]:
    task_id = new_id("task")
    subject = _subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id=policy.stages[0].stage_id,
        subject=subject,
        result_id=subject.subject_id,
        correlation_id=task_id,
        producer=producer,
    )
    return request.verification_id, subject, task_id


@pytest.mark.parametrize(
    ("rules", "verifier"),
    [
        (
            ReviewerIndependence(producer_agent_must_differ=True),
            VerifierIdentity(
                verifier_ref="agent:reviewer@1",
                kind=VerifierKind.AGENT,
                agent_id=new_id("agent"),
                agent_revision=1,
                read_only=True,
            ),
        ),
        (
            ReviewerIndependence(model_must_differ=True),
            VerifierIdentity(
                verifier_ref="agent:reviewer@1",
                kind=VerifierKind.AGENT,
                agent_id=new_id("agent"),
                agent_revision=1,
                model_config_id="review-model",
                read_only=True,
            ),
        ),
        (
            ReviewerIndependence(provider_must_differ=True),
            VerifierIdentity(
                verifier_ref="provider:reviewer",
                kind=VerifierKind.PROVIDER,
                provider_id="review-provider",
                read_only=True,
            ),
        ),
        (
            ReviewerIndependence(human_reviewer_must_differ=True),
            VerifierIdentity(
                verifier_ref="user:reviewer",
                kind=VerifierKind.HUMAN,
                read_only=True,
            ),
        ),
    ],
)
def test_independence_fails_closed_without_required_producer_identity(
    rules: ReviewerIndependence,
    verifier: VerifierIdentity,
) -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="fail-closed independence",
            stages=(VerificationStage("review", verifier.kind),),
            independence=rules,
        )
    )
    verification_id, _subject_value, _task_id = _request(service, policy)
    with pytest.raises(ContractError) as error:
        service.validate_verifier(verification_id, verifier)
    assert error.value.code is ErrorCode.FORBIDDEN


def test_human_separation_and_generic_self_verification_are_policy_enforced() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="separate human",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            independence=ReviewerIndependence(
                human_reviewer_must_differ=True,
                forbid_self_verification=True,
            ),
        )
    )
    verification_id, subject, task_id = _request(
        service,
        policy,
        producer=ProducerIdentity(actor_ref="user:producer"),
    )
    with pytest.raises(ContractError) as self_review:
        service.record_human_review(
            verification_id,
            reviewer_ref="user:producer",
            outcome=VerificationOutcome.PASS,
        )
    assert self_review.value.code is ErrorCode.FORBIDDEN
    service.record_human_review(
        verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
    )
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )


def test_n_independent_reviewers_require_n_distinct_identities() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="two reviewers",
            stages=(
                VerificationStage(
                    "review",
                    VerifierKind.HUMAN,
                    minimum_results=2,
                ),
            ),
            independence=ReviewerIndependence(require_distinct_verifiers=True),
        )
    )
    task_id = new_id("task")
    subject = _subject()

    def create(correlation: str) -> str:
        return service.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id=correlation,
        ).verification_id

    first = create("first")
    second = create("second")
    service.record_human_review(first, reviewer_ref="user:a", outcome=VerificationOutcome.PASS)
    service.record_human_review(second, reviewer_ref="user:a", outcome=VerificationOutcome.PASS)
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.WAITING
    )
    third = create("third")
    service.record_human_review(third, reviewer_ref="user:b", outcome=VerificationOutcome.PASS)
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )


@pytest.mark.parametrize(
    ("timeout_policy", "expected"),
    [
        (VerificationFailurePolicy.WAIT, CompletionState.WAITING),
        (VerificationFailurePolicy.FAIL, CompletionState.REJECTED),
        (VerificationFailurePolicy.ESCALATE, CompletionState.ESCALATED),
    ],
)
def test_request_timeout_uses_explicit_policy_semantics(
    timeout_policy: VerificationFailurePolicy,
    expected: CompletionState,
) -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="timeout policy",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            request_timeout_seconds=5,
            timeout_failure_policy=timeout_policy,
        )
    )
    created = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
    verification_id, subject, task_id = _request(service, policy)
    request = service.get_request(verification_id)
    # Re-create with a fixed time so the timeout boundary is deterministic.
    service = VerificationService()
    service.register_policy(policy)
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id="timeout",
        now=created,
    )
    decision = service.assess_completion(
        task_id=task_id,
        subject=subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        now=created + timedelta(seconds=6),
    )
    assert service.get_request(request.verification_id, now=created + timedelta(seconds=6)).status is VerificationRequestStatus.EXPIRED
    assert decision.state is expected
    assert decision.blocking_verification_ids == (request.verification_id,)


def test_request_cancellation_is_idempotent_auditable_and_terminal() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="cancel",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    verification_id, _subject_value, _task_id = _request(service, policy)
    cancelled = service.cancel_request(verification_id, causation_id="user-cancel")
    assert cancelled.status is VerificationRequestStatus.CANCELLED
    assert service.cancel_request(verification_id) == cancelled
    assert [event.event_type for event in service.audit_history(verification_id=verification_id)] == [
        VerificationAuditEventType.REQUESTED,
        VerificationAuditEventType.REQUEST_CANCELLED,
    ]
    with pytest.raises(ContractError) as completed:
        service.record_human_review(
            verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )
    assert completed.value.code is ErrorCode.CONFLICT


def test_result_subject_is_derived_from_terminal_canonical_run_output() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
        )
        task = await kernel.create_task(
            idempotency_key="subject:create",
            title="Canonical subject",
            objective="Resolve exact Result evidence",
            owner_type="user",
            owner_id="issue-86",
        )
        await kernel.ready_task(idempotency_key="subject:ready", task_id=task.task_id)
        run = await kernel.start_task(idempotency_key="subject:start", task_id=task.task_id)
        lifecycle.complete(
            run.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": 42, "nested": {"ok": True}},
        )
        await kernel.refresh_run(
            idempotency_key="subject:refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        result_id = new_id("result")
        await kernel.attach_result(
            idempotency_key="subject:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
        files = LocalFileProvider(Path("/tmp") / new_id("file"), Path("/tmp") / f"{new_id('file')}.sqlite")
        resolver = KernelFileVerificationEvidenceResolver(kernel, repository, files)
        resolved = await resolver.resolve_subject(
            task_id=task.task_id,
            subject_type="result",
            subject_id=result_id,
        )
        assert resolved.subject_id == result_id
        assert resolved.revision == f"{run.run_id}:attempt:1"
        assert resolved.digest.startswith("sha256:")
        assert resolved != VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="forged",
            digest="sha256:forged",
        )

    asyncio.run(scenario())


def test_file_backed_artifact_subject_and_evidence_validate_real_checksum(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        task = await kernel.create_task(
            idempotency_key="artifact:create",
            title="Artifact evidence",
            objective="Resolve canonical file-backed Artifact",
            owner_type="user",
            owner_id="issue-86",
        )
        artifact_id = new_id("artifact")
        await kernel.attach_artifact(
            idempotency_key="artifact:attach",
            task_id=task.task_id,
            artifact_id=artifact_id,
        )
        files_root = tmp_path / "files"
        files = LocalFileProvider(files_root, tmp_path / "files.sqlite")
        context = DataAccessContext(
            operation=OperationContext(
                correlation_id=task.task_id,
                owner_type="user",
                owner_id="issue-86",
            ),
            actor_ref="user:issue-86",
            task_id=task.task_id,
        )
        record = await files.create_file(b"canonical evidence", context)
        await files.link_artifact(record.file_id, artifact_id, context)
        resolver = KernelFileVerificationEvidenceResolver(kernel, repository, files)
        subject = await resolver.resolve_subject(
            task_id=task.task_id,
            subject_type="artifact",
            subject_id=artifact_id,
        )
        assert subject.revision == record.file_id
        assert subject.digest == f"sha256:{record.sha256}"
        assert await resolver.validate_evidence_artifacts(
            task_id=task.task_id,
            artifact_ids=(artifact_id,),
        ) == (artifact_id,)

        (files_root / record.file_id).write_bytes(b"tampered")
        with pytest.raises(ContractError) as mismatch:
            await resolver.resolve_subject(
                task_id=task.task_id,
                subject_type="artifact",
                subject_id=artifact_id,
            )
        assert mismatch.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())


def test_single_node_deployment_enables_durable_verification_end_to_end(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        assert "verifications" in deployment.control_plane.registered_collections
        assert "verification-reviews" in deployment.control_plane.registered_collections
        assert "verification-requirements" in deployment.control_plane.registered_collections

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="single-node required review",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        task = await deployment.kernel.create_task(
            idempotency_key="verification-deploy:create",
            title="Deployment verification",
            objective="Prove standard deployment gates completion",
            owner_type="user",
            owner_id=admin.user_id,
        )
        deployment.verification_completion.require_task(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )
        await deployment.kernel.ready_task(
            idempotency_key="verification-deploy:ready",
            task_id=task.task_id,
        )
        run = await deployment.kernel.start_task(
            idempotency_key="verification-deploy:start",
            task_id=task.task_id,
        )
        await deployment.kernel.refresh_run(
            idempotency_key="verification-deploy:refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        blocked = await deployment.kernel.get_task(task.task_id)
        assert blocked.status is TaskStatus.WAITING
        assert blocked.wait_reason == "verification:waiting"

        result_id = new_id("result")
        await deployment.kernel.attach_result(
            idempotency_key="verification-deploy:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
        request = await deployment.verification_runtime.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=result_id,
            correlation_id=task.task_id,
            run_id=run.run_id,
        )
        assert request.subject.digest.startswith("sha256:")
        deployment.verification.record_human_review(
            request.verification_id,
            reviewer_ref=f"user:{admin.user_id}:reviewer",
            outcome=VerificationOutcome.PASS,
        )
        completed = await deployment.kernel.complete_task(
            idempotency_key="verification-deploy:complete",
            task_id=task.task_id,
        )
        assert completed.status is TaskStatus.SUCCEEDED

        restarted = build_single_node_deployment(config)
        restored = restarted.verification.get_request(request.verification_id)
        assert restored.subject == request.subject
        assert restarted.verification.result_for(request.verification_id) is not None
        assert (
            restarted.verification_completion.assess_task_completion(task.task_id).state
            is CompletionState.ACCEPTED
        )
        assert "verifications" in restarted.control_plane.registered_collections

    asyncio.run(scenario())
'''
Path("tests/test_issue_86_hardening.py").write_text(test_source, encoding="utf-8")
