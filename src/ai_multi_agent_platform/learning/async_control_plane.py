"""Async record-scoped Control Plane surfaces for governed Learning runtime paths."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    CommandHandler,
    ControlPlane,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security import RiskClassification
from ai_multi_agent_platform.security.redaction import redact_sensitive

from .async_service import RuntimeGovernedLearningService
from .control_plane import (
    LEARNING_CANDIDATE_COLLECTION,
    LEARNING_FEEDBACK_COLLECTION,
    _actor,
    _approval_resource,
    _enum,
    _feedback_resource,
    _gate_plan,
    _json_object,
    _operation,
    _optional_reference,
    _optional_string,
    _optional_target,
    _reference,
    _reference_tuple,
    _require_collection,
    _require_idempotency_key,
    _required_object,
    _required_positive_int,
    _required_string,
    _string_tuple,
    _target,
)
from .models import (
    FeedbackType,
    LearningCandidate,
    LearningReference,
    LearningSourceType,
    LearningTarget,
    candidate_to_dict,
)
from .runtime import PostPromotionEvaluationRecord
from .runtime_adapter import LearningRuntimeAdapter
from .runtime_control_plane import LEARNING_POST_PROMOTION_COLLECTION
from .runtime_control_plane import _resource as _post_resource
from .scoped_control_plane import (
    LearningScopeAccess,
    _install_deferred_authorization,
    _not_found,
    _payload_project_id,
    _payload_target,
    _require_matching_evaluation_evidence_project_scope,
    _require_matching_evidence_project_scope,
    _require_matching_project_scope,
    _required_payload_string,
)


class AsyncLearningCandidateResourceService(ResourceService):
    def __init__(self, runtime: LearningRuntimeAdapter, access: LearningScopeAccess) -> None:
        self._runtime = runtime
        self._access = access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for candidate in await self._runtime.list_candidates():
            if not await self._access.allowed(
                context,
                "learning-candidate:list",
                candidate.learning_candidate_id,
                project_id=candidate.project_id,
            ):
                continue
            resources.append(await _candidate_resource(self._runtime, candidate))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        candidate = await self._runtime.get_candidate(resource_id)
        if not await self._access.allowed(
            context,
            "learning-candidate:read",
            resource_id,
            project_id=candidate.project_id,
        ):
            _not_found("Learning Candidate")
        return await _candidate_resource(self._runtime, candidate)


class AsyncLearningFeedbackResourceService(ResourceService):
    def __init__(self, runtime: LearningRuntimeAdapter, access: LearningScopeAccess) -> None:
        self._runtime = runtime
        self._access = access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for feedback in await self._runtime.list_feedback():
            if await self._access.allowed(
                context,
                "learning-feedback:list",
                feedback.feedback_id,
                project_id=feedback.project_id,
            ):
                resources.append(_feedback_resource(feedback))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        feedback = await self._runtime.get_feedback(resource_id)
        if not await self._access.allowed(
            context,
            "learning-feedback:read",
            resource_id,
            project_id=feedback.project_id,
        ):
            _not_found("Learning feedback")
        return _feedback_resource(feedback)


class AsyncLearningPostPromotionResourceService(ResourceService):
    def __init__(self, runtime: LearningRuntimeAdapter, access: LearningScopeAccess) -> None:
        self._runtime = runtime
        self._access = access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for candidate in await self._runtime.list_candidates():
            for record in await self._runtime.list_post_promotion_records(
                candidate.learning_candidate_id
            ):
                if not await self._access.allowed(
                    context,
                    "learning-post-promotion-evaluation:list",
                    record.record_id,
                    project_id=candidate.project_id,
                ):
                    continue
                resources.append(_post_resource(record))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for candidate in await self._runtime.list_candidates():
            for record in await self._runtime.list_post_promotion_records(
                candidate.learning_candidate_id
            ):
                if record.record_id != resource_id:
                    continue
                if not await self._access.allowed(
                    context,
                    "learning-post-promotion-evaluation:read",
                    resource_id,
                    project_id=candidate.project_id,
                ):
                    _not_found("post-promotion Learning Evaluation record")
                return _post_resource(record)
        _not_found("post-promotion Learning Evaluation record")


class AsyncScopedLearningCommand:
    def __init__(
        self,
        action: str,
        delegate: CommandHandler,
        runtime: LearningRuntimeAdapter,
        access: LearningScopeAccess,
    ) -> None:
        self.action = action
        self.delegate = delegate
        self.runtime = runtime
        self.access = access

    async def __call__(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize(context, resource_ref, payload)
        return await self.delegate(context, resource_ref, payload)

    async def _authorize(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        if self.action == "learning.feedback.create":
            await self.access.authorize(
                context,
                self.action,
                resource_ref,
                project_id=_payload_project_id(payload),
            )
            return

        if self.action == "learning.propose":
            project_id = _payload_project_id(payload)
            await self.access.authorize(
                context,
                self.action,
                resource_ref,
                project_id=project_id,
            )
            await self._require_supported_target_scope(_payload_target(payload), project_id)
            return

        if self.action == "learning.propose-from-feedback":
            feedback = await self.runtime.get_feedback(resource_ref)
            await self.access.authorize(
                context,
                self.action,
                resource_ref,
                project_id=feedback.project_id,
            )
            await self._require_supported_target_scope(
                _payload_target(payload),
                feedback.project_id,
            )
            return

        candidate = await self.runtime.get_candidate(resource_ref)
        await self.access.authorize(
            context,
            self.action,
            resource_ref,
            project_id=candidate.project_id,
        )
        if self.action == "learning.evidence":
            for evaluation_run_id in _string_tuple(
                payload.get("evaluation_run_ids"),
                "evaluation_run_ids",
            ):
                evaluation_project_id = await self.runtime.evaluation_run_project_id(
                    evaluation_run_id
                )
                _require_matching_evaluation_evidence_project_scope(
                    candidate.project_id,
                    evaluation_project_id,
                )
                await self.access.authorize(
                    context,
                    self.action,
                    evaluation_run_id,
                    project_id=evaluation_project_id,
                )
            for verification_id in _string_tuple(
                payload.get("verification_ids"),
                "verification_ids",
            ):
                request = await self.runtime.verification_request(verification_id)
                if request is None:
                    raise ContractError(
                        ErrorCode.UNAVAILABLE,
                        "Verification service is not configured",
                    )
                _require_matching_evidence_project_scope(
                    candidate.project_id,
                    request.project_id,
                )
                await self.access.authorize(
                    context,
                    self.action,
                    verification_id,
                    project_id=request.project_id,
                )
            return

        if self.action == "learning.promote":
            target_project_id = await self.runtime.target_project_id(candidate.target)
            _require_matching_project_scope(candidate.project_id, target_project_id)
            await self.access.authorize(
                context,
                self.action,
                candidate.target.resource_id,
                project_id=target_project_id,
            )
            return

        if self.action != "learning.supersede":
            return
        replacement_id = _required_payload_string(payload, "superseded_by")
        replacement = await self.runtime.get_candidate(replacement_id)
        if not await self.access.allowed(
            context,
            "learning-candidate:read",
            replacement_id,
            project_id=replacement.project_id,
        ):
            _not_found("Learning Candidate")

    async def _require_supported_target_scope(
        self,
        target: LearningTarget,
        candidate_project_id: str | None,
    ) -> None:
        registry = self.runtime.service.promotion_registry
        if not registry.supports(target.resource_type):
            return
        target_project_id = await self.runtime.target_project_id(target)
        _require_matching_project_scope(candidate_project_id, target_project_id)


def register_async_scoped_learning_control_plane(
    control_plane: ControlPlane,
    learning: RuntimeGovernedLearningService,
) -> None:
    """Register production Learning surfaces without inline synchronous persistence."""

    runtime = LearningRuntimeAdapter(learning)
    access = _install_deferred_authorization(control_plane)

    control_plane.register_resource_service(
        LEARNING_CANDIDATE_COLLECTION,
        AsyncLearningCandidateResourceService(runtime, access),
    )
    control_plane.register_resource_service(
        LEARNING_FEEDBACK_COLLECTION,
        AsyncLearningFeedbackResourceService(runtime, access),
    )
    control_plane.register_resource_service(
        LEARNING_POST_PROMOTION_COLLECTION,
        AsyncLearningPostPromotionResourceService(runtime, access),
    )

    handlers = _handlers(runtime)
    for action, handler in handlers.items():
        control_plane.register_command(
            action,
            AsyncScopedLearningCommand(action, handler, runtime, access),
        )


def _handlers(runtime: LearningRuntimeAdapter) -> dict[str, CommandHandler]:
    async def create_feedback(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, LEARNING_FEEDBACK_COLLECTION)
        feedback, _ = await runtime.record_feedback(
            feedback_type=_enum(FeedbackType, payload, "feedback_type"),
            subject=_reference(_required_object(payload, "subject")),
            creator_ref=context.actor.principal_ref,
            comment=_optional_string(payload.get("comment"), "comment"),
            target=_optional_target(payload.get("target")),
            project_id=_optional_string(payload.get("project_id"), "project_id"),
        )
        return _feedback_resource(feedback)

    async def propose(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, LEARNING_CANDIDATE_COLLECTION)
        source_refs = _reference_tuple(payload.get("source_refs"), "source_refs")
        if not source_refs:
            source_refs = (
                LearningReference(
                    kind="control_plane_operator_proposal",
                    resource_id=_require_idempotency_key(context),
                ),
            )
        candidate, _ = await runtime.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem=_required_string(payload, "problem"),
            target=_target(_required_object(payload, "target")),
            improvement_type=_required_string(payload, "improvement_type"),
            expected_benefit=_required_string(payload, "expected_benefit"),
            risk=_enum(RiskClassification, payload, "risk"),
            gate_plan=_gate_plan(_required_object(payload, "gate_plan")),
            creator_ref=context.actor.principal_ref,
            source_refs=source_refs,
            evidence_refs=_reference_tuple(payload.get("evidence_refs"), "evidence_refs"),
            proposed_change=_json_object(payload.get("proposed_change"), "proposed_change"),
            proposed_artifact_ref=_optional_reference(payload.get("proposed_artifact_ref")),
            project_id=_optional_string(payload.get("project_id"), "project_id"),
        )
        return await _candidate_resource(runtime, candidate)

    async def propose_from_feedback(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        feedback = await runtime.get_feedback(resource_ref)
        candidate, _ = await runtime.create_from_feedback(
            feedback,
            problem=_required_string(payload, "problem"),
            target=_target(_required_object(payload, "target")),
            improvement_type=_required_string(payload, "improvement_type"),
            expected_benefit=_required_string(payload, "expected_benefit"),
            risk=_enum(RiskClassification, payload, "risk"),
            gate_plan=_gate_plan(_required_object(payload, "gate_plan")),
            creator_ref=context.actor.principal_ref,
            proposed_change=_json_object(payload.get("proposed_change"), "proposed_change"),
            proposed_artifact_ref=_optional_reference(payload.get("proposed_artifact_ref")),
            evidence_refs=_reference_tuple(payload.get("evidence_refs"), "evidence_refs"),
        )
        return await _candidate_resource(runtime, candidate)

    async def record_evidence(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        candidate = await runtime.record_gate_evidence(
            resource_ref,
            evaluation_run_ids=_string_tuple(
                payload.get("evaluation_run_ids"), "evaluation_run_ids"
            ),
            verification_ids=_string_tuple(payload.get("verification_ids"), "verification_ids"),
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return await _candidate_resource(runtime, candidate)

    async def accept(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        candidate = await runtime.accept(
            resource_ref,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return await _candidate_resource(runtime, candidate)

    async def reject(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        candidate = await runtime.reject(
            resource_ref,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return await _candidate_resource(runtime, candidate)

    async def supersede(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        candidate = await runtime.supersede(
            resource_ref,
            superseded_by=_required_payload_string(payload, "superseded_by"),
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return await _candidate_resource(runtime, candidate)

    async def promote(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        candidate = await runtime.get_candidate(resource_ref)
        promoted = await runtime.promote(
            resource_ref,
            actor=_actor(context),
            operation=_operation(context, candidate.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
            automatic=False,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return await _candidate_resource(runtime, promoted)

    return {
        "learning.feedback.create": create_feedback,
        "learning.propose": propose,
        "learning.propose-from-feedback": propose_from_feedback,
        "learning.evidence": record_evidence,
        "learning.accept": accept,
        "learning.reject": reject,
        "learning.supersede": supersede,
        "learning.promote": promote,
    }


async def _candidate_resource(
    runtime: LearningRuntimeAdapter,
    candidate: LearningCandidate,
) -> dict[str, JsonValue]:
    payload = candidate_to_dict(candidate)
    payload["id"] = candidate.learning_candidate_id
    payload["type"] = "learning-candidate"
    history = await runtime.candidate_history(candidate.learning_candidate_id, candidate.revision)
    payload["history"] = [_candidate_history_entry(item) for item in history]
    approvals = await runtime.service.authorization_gate.runtime_approvals.all()
    payload["approvals"] = [
        _approval_resource(record)
        for record in approvals
        if record.payload_ref is not None
        and record.payload_ref.startswith(f"{candidate.learning_candidate_id}@r")
    ]
    payload["post_promotion_regression_status"] = await _post_promotion_status(runtime, candidate)
    redacted = redact_sensitive(payload)
    if not isinstance(redacted, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "Learning candidate redaction returned an invalid projection",
        )
    return redacted


def _candidate_history_entry(candidate: LearningCandidate) -> dict[str, JsonValue]:
    return {
        "revision": candidate.revision,
        "status": candidate.status.value,
        "content_digest": candidate.content_digest,
        "evaluation_run_ids": list(candidate.evaluation_run_ids),
        "verification_ids": list(candidate.verification_ids),
        "superseded_by": candidate.superseded_by,
        "promotion": candidate_to_dict(candidate)["promotion"],
        "updated_at": candidate.updated_at.isoformat(),
    }


async def _post_promotion_status(
    runtime: LearningRuntimeAdapter,
    candidate: LearningCandidate,
) -> str:
    if candidate.promotion is None:
        return "not_applicable" if candidate.status.value != "promoted" else "not_recorded"
    records: tuple[PostPromotionEvaluationRecord, ...] = await runtime.list_post_promotion_records(
        candidate.learning_candidate_id
    )
    for record in records:
        if record.target_revision == candidate.promotion.new_revision:
            return record.outcome.value
    return "not_recorded"


__all__ = ["register_async_scoped_learning_control_plane"]
