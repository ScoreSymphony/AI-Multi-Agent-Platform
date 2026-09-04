"""Control Plane extension for canonical runtime Verification and human review (#86)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.service import _payload_digest
from ai_multi_agent_platform.kernel import TaskState

from .gate import VerificationCompletionAuthority
from .models import (
    VerificationFinding,
    VerificationOutcome,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from .service import VerificationService

VERIFICATION_COLLECTION = "verifications"
VERIFICATION_REVIEW_COLLECTION = "verification-reviews"
VERIFICATION_REQUIREMENT_COLLECTION = "verification-requirements"
VERIFICATION_COMMANDS = (
    "verification.accept",
    "verification.reject",
    "verification.request-changes",
)


class VerificationResourceService(ResourceService):
    """Task-scoped read view of canonical Verification history."""

    def __init__(
        self,
        control_plane: ControlPlane,
        verification: VerificationService,
    ) -> None:
        self._control_plane = control_plane
        self._verification = verification

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[tuple[VerificationRequest, dict[str, JsonValue]]] = []
        for task_id in await _task_ids(self._control_plane):
            task = await self._control_plane._kernel.get_task(task_id)
            if not await _allowed_for_task(
                self._control_plane,
                context,
                "verification:list",
                task,
                resource_ref=task_id,
            ):
                continue
            for request, result in self._verification.history(task_id=task_id):
                resources.append((request, _verification_resource(request, result)))
        resources.sort(key=lambda item: (item[0].created_at, item[0].verification_id))
        return tuple(resource for _request, resource in resources)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate all canonical requests with task scope for Search rebuild."""

        resources: list[tuple[VerificationRequest, dict[str, JsonValue]]] = []
        for task_id in await _task_ids(self._control_plane):
            task = await self._control_plane._kernel.get_task(task_id)
            for request, result in self._verification.history(task_id=task_id):
                resources.append(
                    (
                        request,
                        _search_scoped_resource(
                            _verification_resource(request, result),
                            task,
                        ),
                    )
                )
        resources.sort(key=lambda item: (item[0].created_at, item[0].verification_id))
        return tuple(resource for _request, resource in resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        request = self._verification.get_request(resource_id)
        task = await self._control_plane._kernel.get_task(request.task_id)
        await _authorize_for_task(
            self._control_plane,
            context,
            "verification:read",
            task,
            resource_ref=request.verification_id,
        )
        return _verification_resource(request, self._verification.result_for(resource_id))


class VerificationReviewQueueResourceService(ResourceService):
    """Authorized pending-human-review queue derived from canonical requests."""

    # This collection is a filtered navigation view over the same canonical
    # ``verification`` resources exposed by VERIFICATION_COLLECTION. Indexing it would
    # duplicate one resource type across two canonical collections and create ambiguous
    # Search authorization/canonical refs.
    search_indexable = False

    def __init__(
        self,
        control_plane: ControlPlane,
        verification: VerificationService,
    ) -> None:
        self._control_plane = control_plane
        self._verification = verification

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[tuple[VerificationRequest, dict[str, JsonValue]]] = []
        for task_id in await _task_ids(self._control_plane):
            task = await self._control_plane._kernel.get_task(task_id)
            if not await _allowed_for_task(
                self._control_plane,
                context,
                "verification-review:list",
                task,
                resource_ref=task_id,
            ):
                continue
            for request, result in self._verification.history(task_id=task_id):
                current = self._verification.get_request(request.verification_id)
                if (
                    current.status is VerificationRequestStatus.PENDING
                    and current.requested_verifier_kind is VerifierKind.HUMAN
                ):
                    resources.append((current, _verification_resource(current, result)))
        resources.sort(key=lambda item: (item[0].created_at, item[0].verification_id))
        return tuple(resource for _request, resource in resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        request = self._verification.get_request(resource_id)
        if (
            request.status is not VerificationRequestStatus.PENDING
            or request.requested_verifier_kind is not VerifierKind.HUMAN
        ):
            raise ContractError(ErrorCode.NOT_FOUND, "pending human verification was not found")
        task = await self._control_plane._kernel.get_task(request.task_id)
        await _authorize_for_task(
            self._control_plane,
            context,
            "verification-review:read",
            task,
            resource_ref=request.verification_id,
        )
        return _verification_resource(request, self._verification.result_for(resource_id))


class VerificationRequirementResourceService(ResourceService):
    """Task-scoped completion-policy status for UI and automation consumers."""

    def __init__(
        self,
        control_plane: ControlPlane,
        completion: VerificationCompletionAuthority,
    ) -> None:
        self._control_plane = control_plane
        self._completion = completion

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for task_id in await _task_ids(self._control_plane):
            requirement = self._completion.requirement_for(task_id)
            if requirement is None:
                continue
            task = await self._control_plane._kernel.get_task(task_id)
            if not await _allowed_for_task(
                self._control_plane,
                context,
                "verification-requirement:list",
                task,
                resource_ref=task_id,
            ):
                continue
            resources.append(_requirement_resource(self._completion, task_id))
        return tuple(resources)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate all canonical requirements with task scope for Search rebuild."""

        resources: list[dict[str, JsonValue]] = []
        for task_id in await _task_ids(self._control_plane):
            if self._completion.requirement_for(task_id) is None:
                continue
            task = await self._control_plane._kernel.get_task(task_id)
            resources.append(
                _search_scoped_resource(
                    _requirement_resource(self._completion, task_id),
                    task,
                )
            )
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        requirement = self._completion.requirement_for(resource_id)
        if requirement is None:
            raise ContractError(ErrorCode.NOT_FOUND, "verification requirement was not found")
        task = await self._control_plane._kernel.get_task(resource_id)
        await _authorize_for_task(
            self._control_plane,
            context,
            "verification-requirement:read",
            task,
            resource_ref=resource_id,
        )
        return _requirement_resource(self._completion, resource_id)


class VerificationCommandHandlers:
    """Canonical human-review commands with object-scoped #15 authorization."""

    def __init__(
        self,
        control_plane: ControlPlane,
        verification: VerificationService,
    ) -> None:
        self._control_plane = control_plane
        self._verification = verification

    async def accept(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._record_human_review(
            context,
            resource_ref,
            payload,
            outcome=VerificationOutcome.PASS,
            action="verification.accept",
        )

    async def reject(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._record_human_review(
            context,
            resource_ref,
            payload,
            outcome=VerificationOutcome.FAIL,
            action="verification.reject",
        )

    async def request_changes(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._record_human_review(
            context,
            resource_ref,
            payload,
            outcome=VerificationOutcome.NEEDS_CHANGES,
            action="verification.request-changes",
        )

    async def _record_human_review(
        self,
        context: RequestContext,
        verification_id: str,
        payload: dict[str, JsonValue],
        *,
        outcome: VerificationOutcome,
        action: str,
    ) -> dict[str, JsonValue]:
        request = self._verification.get_request(verification_id)
        if request.requested_verifier_kind is not VerifierKind.HUMAN:
            raise ContractError(
                ErrorCode.CONFLICT,
                "verification request is not assigned to a human reviewer",
            )
        task = await self._control_plane._kernel.get_task(request.task_id)
        digest = _payload_digest(payload)
        await _authorize_for_task(
            self._control_plane,
            context,
            action,
            task,
            resource_ref=request.verification_id,
            request_payload_digest=digest,
        )
        key = context.idempotency_key
        if key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for human verification commands",
            )

        existing = self._verification.result_for(verification_id)
        if existing is not None:
            if _is_same_control_plane_review(
                existing,
                context=context,
                idempotency_key=key,
                payload_digest=digest,
                outcome=outcome,
            ):
                return _verification_resource(request, existing)
            raise ContractError(
                ErrorCode.CONFLICT,
                "verification request already has a different canonical result",
            )

        comment = _optional_comment(payload)
        findings: tuple[VerificationFinding, ...] = ()
        if comment is not None:
            findings = (
                VerificationFinding(
                    code="human_review_comment",
                    message=comment,
                    severity="info" if outcome is VerificationOutcome.PASS else "warning",
                ),
            )
        result = self._verification.submit_result(
            VerificationResult(
                verification_id=verification_id,
                verifier=VerifierIdentity(
                    verifier_ref=context.actor.principal_ref,
                    kind=VerifierKind.HUMAN,
                    read_only=True,
                ),
                outcome=outcome,
                subject=request.subject,
                findings=findings,
                evidence_artifact_ids=_artifact_ids(payload),
                checks_executed=("human_review",),
                metadata={
                    "control_plane": {
                        "idempotency_key": key,
                        "payload_digest": digest,
                        "actor_ref": context.actor.principal_ref,
                        "action": action,
                    }
                },
            )
        )
        return _verification_resource(self._verification.get_request(verification_id), result)


def register_verification_control_plane(
    control_plane: ControlPlane,
    verification: VerificationService,
    completion: VerificationCompletionAuthority,
) -> None:
    """Register #86 read/review surfaces on the generic #32 extension seam."""

    control_plane.register_resource_service(
        VERIFICATION_COLLECTION,
        VerificationResourceService(control_plane, verification),
    )
    control_plane.register_resource_service(
        VERIFICATION_REVIEW_COLLECTION,
        VerificationReviewQueueResourceService(control_plane, verification),
    )
    control_plane.register_resource_service(
        VERIFICATION_REQUIREMENT_COLLECTION,
        VerificationRequirementResourceService(control_plane, completion),
    )
    handlers = VerificationCommandHandlers(control_plane, verification)
    control_plane.register_command("verification.accept", handlers.accept)
    control_plane.register_command("verification.reject", handlers.reject)
    control_plane.register_command("verification.request-changes", handlers.request_changes)


async def _task_ids(control_plane: ControlPlane) -> tuple[str, ...]:
    return tuple(
        stream_id
        for stream_id in await control_plane._events.list_stream_ids()
        if stream_id.startswith("task_")
    )


async def _authorize_for_task(
    control_plane: ControlPlane,
    context: RequestContext,
    action: str,
    task: TaskState,
    *,
    resource_ref: str,
    request_payload_digest: str | None = None,
) -> None:
    await control_plane._authorize(
        context,
        action,
        resource_ref,
        owner_type=task.task.owner_ref.type,
        owner_id=task.task.owner_ref.id,
        project_id=task.task.project_id,
        request_payload_digest=request_payload_digest,
    )


async def _allowed_for_task(
    control_plane: ControlPlane,
    context: RequestContext,
    action: str,
    task: TaskState,
    *,
    resource_ref: str,
) -> bool:
    return await control_plane._allowed(
        context,
        action,
        resource_ref,
        owner_type=task.task.owner_ref.type,
        owner_id=task.task.owner_ref.id,
        project_id=task.task.project_id,
    )


def _search_scoped_resource(
    resource: dict[str, JsonValue],
    task: TaskState,
) -> dict[str, JsonValue]:
    """Add only canonical task authorization scope to an internal Search projection."""

    scoped = dict(resource)
    scoped["owner_type"] = task.task.owner_ref.type
    scoped["owner_id"] = task.task.owner_ref.id
    scoped["project_id"] = task.task.project_id
    return scoped


def _verification_resource(
    request: VerificationRequest,
    result: VerificationResult | None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": request.verification_id,
        "type": "verification",
        "task_id": request.task_id,
        "run_id": request.run_id,
        "result_id": request.result_id,
        "artifact_ids": list(request.artifact_ids),
        "project_id": request.project_id,
        "capability_ids": list(request.capability_ids),
        "policy": {"id": request.policy_id, "version": request.policy_version},
        "stage_id": request.stage_id,
        "subject": _subject_resource(request.subject),
        "requested_verifier_kind": request.requested_verifier_kind.value,
        "requested_capability_ref": request.requested_capability_ref,
        "repair_attempt": request.repair_attempt,
        "status": request.status.value,
        "created_at": request.created_at.isoformat(),
        "expires_at": None if request.expires_at is None else request.expires_at.isoformat(),
        "correlation_id": request.correlation_id,
        "causation_id": request.causation_id,
    }
    if request.producer is not None:
        payload["producer"] = {
            "actor_ref": request.producer.actor_ref,
            "agent_id": request.producer.agent_id,
            "agent_revision": request.producer.agent_revision,
            "model_config_id": request.producer.model_config_id,
            "provider_id": request.producer.provider_id,
        }
    if result is not None:
        payload["verification_result"] = _result_resource(result)
    return payload


def _result_resource(result: VerificationResult) -> dict[str, JsonValue]:
    return {
        "id": result.verification_result_id,
        "verification_id": result.verification_id,
        "outcome": result.outcome.value,
        "subject": _subject_resource(result.subject),
        "verifier": {
            "ref": result.verifier.verifier_ref,
            "kind": result.verifier.kind.value,
            "agent_id": result.verifier.agent_id,
            "agent_revision": result.verifier.agent_revision,
            "model_config_id": result.verifier.model_config_id,
            "provider_id": result.verifier.provider_id,
            "read_only": result.verifier.read_only,
        },
        "findings": [
            {
                "code": finding.code,
                "message": finding.message,
                "severity": finding.severity,
                "location_ref": finding.location_ref,
            }
            for finding in result.findings
        ],
        "evidence_artifact_ids": list(result.evidence_artifact_ids),
        "checks_executed": list(result.checks_executed),
        "errors": [
            {"code": error.code, "message": error.message, "retryable": error.retryable}
            for error in result.errors
        ],
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat(),
        "metadata": dict(result.metadata),
    }


def _requirement_resource(
    completion: VerificationCompletionAuthority,
    task_id: str,
) -> dict[str, JsonValue]:
    requirement = completion.requirement_for(task_id)
    if requirement is None:
        raise ContractError(ErrorCode.NOT_FOUND, "verification requirement was not found")
    decision = completion.assess_task_completion(task_id)
    return {
        "id": task_id,
        "type": "verification_requirement",
        "task_id": task_id,
        "policy": {"id": requirement.policy_id, "version": requirement.policy_version},
        "subject": None if requirement.subject is None else _subject_resource(requirement.subject),
        "created_at": requirement.created_at.isoformat(),
        "updated_at": requirement.updated_at.isoformat(),
        "completion": {
            "state": decision.state.value,
            "reason": decision.reason,
            "blocking_verification_ids": list(decision.blocking_verification_ids),
            "repair_attempts_remaining": decision.repair_attempts_remaining,
        },
    }


def _subject_resource(subject: VerificationSubject) -> dict[str, JsonValue]:
    return {
        "type": subject.subject_type,
        "id": subject.subject_id,
        "revision": subject.revision,
        "digest": subject.digest,
    }


def _artifact_ids(payload: Mapping[str, JsonValue]) -> tuple[str, ...]:
    raw = payload.get("evidence_artifact_ids", [])
    if not isinstance(raw, list | tuple):
        raise ValueError("evidence_artifact_ids must be an array")
    artifact_ids: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("evidence_artifact_ids must contain non-blank strings")
        artifact_ids.append(item)
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("evidence_artifact_ids must be unique")
    return tuple(artifact_ids)


def _optional_comment(payload: Mapping[str, JsonValue]) -> str | None:
    value = payload.get("comment")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("comment must be a non-blank string")
    return value


def _is_same_control_plane_review(
    result: VerificationResult,
    *,
    context: RequestContext,
    idempotency_key: str,
    payload_digest: str,
    outcome: VerificationOutcome,
) -> bool:
    if result.outcome is not outcome or result.verifier.verifier_ref != context.actor.principal_ref:
        return False
    raw = result.metadata.get("control_plane")
    if not isinstance(raw, Mapping):
        return False
    metadata = cast(Mapping[str, object], raw)
    return (
        metadata.get("idempotency_key") == idempotency_key
        and metadata.get("payload_digest") == payload_digest
        and metadata.get("actor_ref") == context.actor.principal_ref
    )