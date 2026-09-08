"""Control Plane projection for optional post-promotion Learning Evaluation (#595)."""

from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security.redaction import redact_sensitive

from .control_plane import LEARNING_CANDIDATE_COLLECTION, LearningCandidateResourceService
from .runtime import ObservedLearningService, PostPromotionEvaluationRecord

LEARNING_POST_PROMOTION_COLLECTION = "learning-post-promotion-evaluations"


class RuntimeAwareLearningCandidateResourceService(LearningCandidateResourceService):
    """Enrich the canonical Candidate projection with durable post-promotion status."""

    def __init__(self, learning: ObservedLearningService) -> None:
        super().__init__(learning)
        self._observed_learning = learning

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        resources = await super().list_resources(context, query)
        return tuple(self._with_post_promotion_status(resource) for resource in resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        resource = await super().get_resource(context, resource_id)
        return self._with_post_promotion_status(resource)

    def _with_post_promotion_status(
        self,
        resource: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        candidate_id = resource.get("learning_candidate_id")
        promotion = resource.get("promotion")
        if not isinstance(candidate_id, str) or not isinstance(promotion, dict):
            resource["post_promotion_regression_status"] = "not_applicable"
            return resource
        target_revision = promotion.get("new_revision")
        if isinstance(target_revision, bool) or not isinstance(target_revision, int):
            resource["post_promotion_regression_status"] = "not_recorded"
            return resource
        record = self._observed_learning.post_promotion_recorder.record_for_promotion(
            candidate_id,
            target_revision,
        )
        resource["post_promotion_regression_status"] = (
            "not_recorded" if record is None else record.outcome.value
        )
        return resource


class LearningPostPromotionResourceService(ResourceService):
    def __init__(self, learning: ObservedLearningService) -> None:
        self._learning = learning

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        records: list[dict[str, JsonValue]] = []
        for candidate in self._learning.list_candidates():
            records.extend(
                _resource(record)
                for record in self._learning.post_promotion_recorder.list_for_candidate(
                    candidate.learning_candidate_id
                )
            )
        return tuple(records)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        for candidate in self._learning.list_candidates():
            for record in self._learning.post_promotion_recorder.list_for_candidate(
                candidate.learning_candidate_id
            ):
                if record.record_id == resource_id:
                    return _resource(record)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            "post-promotion Learning Evaluation record was not found",
        )


def register_learning_runtime_control_plane(
    control_plane: ControlPlane,
    learning: ObservedLearningService,
) -> None:
    """Register derived post-promotion evidence without adding mutation authority."""

    control_plane.register_resource_service(
        LEARNING_CANDIDATE_COLLECTION,
        RuntimeAwareLearningCandidateResourceService(learning),
    )
    control_plane.register_resource_service(
        LEARNING_POST_PROMOTION_COLLECTION,
        LearningPostPromotionResourceService(learning),
    )


def _resource(record: PostPromotionEvaluationRecord) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": record.record_id,
        "type": "learning-post-promotion-evaluation",
        "learning_candidate_id": record.learning_candidate_id,
        "candidate_revision": record.candidate_revision,
        "target_revision": record.target_revision,
        "outcome": record.outcome.value,
        "evaluation_run_ids": list(record.evaluation_run_ids),
        "details": dict(record.details),
        "created_at": record.created_at.isoformat(),
    }
    safe = redact_sensitive(payload)
    if not isinstance(safe, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "post-promotion Learning projection redaction returned invalid data",
        )
    return cast(dict[str, JsonValue], safe)
