"""Additive single-node composition seam for governed Learning (#595)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.evaluation import EvaluationService
from ai_multi_agent_platform.models import ModelRoutingProfileService
from ai_multi_agent_platform.observability import Telemetry
from ai_multi_agent_platform.research import ResearchService
from ai_multi_agent_platform.security import AuthorizationGate
from ai_multi_agent_platform.skills import (
    SKILL_COLLECTION,
    JsonSkillRepository,
    SkillService,
    register_skill_control_plane,
)
from ai_multi_agent_platform.verification import VerificationService

from .control_plane import LEARNING_COLLECTIONS, register_learning_control_plane
from .governance import GovernedObservedLearningService, LearningPlatformPolicy
from .post_promotion_repository import SQLitePostPromotionEvaluationRecorder
from .promotion import (
    AgentPromotionAdapter,
    PromotionRegistry,
    RoutingProfilePromotionAdapter,
    SkillPromotionAdapter,
)
from .repository import SQLiteLearningRepository
from .runtime import PostPromotionEvaluator
from .runtime_control_plane import (
    LEARNING_POST_PROMOTION_COLLECTION,
    register_learning_runtime_control_plane,
)
from .service import LearningQualityGate
from .sources import LearningSourceBridge


@dataclass(frozen=True, slots=True)
class SingleNodeLearningComposition:
    """Prepared owner-preserving Learning composition for the shared single-node runtime."""

    repository: SQLiteLearningRepository
    post_promotion_recorder: SQLitePostPromotionEvaluationRecorder
    skills: SkillService
    service: GovernedObservedLearningService
    sources: LearningSourceBridge

    def register_control_plane(
        self,
        control_plane: ControlPlane,
        *,
        register_skills: bool = True,
    ) -> None:
        """Register only missing additive surfaces on the shared canonical Control Plane."""

        registered = set(control_plane.registered_collections)
        if register_skills and SKILL_COLLECTION not in registered:
            register_skill_control_plane(control_plane, self.skills)
            registered = set(control_plane.registered_collections)
        if not set(LEARNING_COLLECTIONS).issubset(registered):
            register_learning_control_plane(control_plane, self.service)
            registered = set(control_plane.registered_collections)
        if LEARNING_POST_PROMOTION_COLLECTION not in registered:
            register_learning_runtime_control_plane(control_plane, self.service)


def build_single_node_learning(
    *,
    database_dir: str | Path,
    agents: AgentService,
    routing_profiles: ModelRoutingProfileService,
    evaluation: EvaluationService,
    verification: VerificationService,
    approval_gate: AuthorizationGate,
    telemetry: Telemetry | None = None,
    skills: SkillService | None = None,
    research: ResearchService | None = None,
    platform_policy: LearningPlatformPolicy | None = None,
    post_promotion_evaluator: PostPromotionEvaluator | None = None,
) -> SingleNodeLearningComposition:
    """Compose #595 around existing canonical owner services without replacing them."""

    root = Path(database_dir)
    skill_service = skills or SkillService(JsonSkillRepository(root / "skills.json"))
    repository = SQLiteLearningRepository(root / "learning.sqlite3")
    post_promotion_recorder = SQLitePostPromotionEvaluationRecorder(
        root / "learning-post-promotion.sqlite3"
    )
    service = GovernedObservedLearningService(
        repository,
        quality_gate=LearningQualityGate(
            evaluation=evaluation,
            verification=verification,
        ),
        promotion_registry=PromotionRegistry(
            (
                AgentPromotionAdapter(agents),
                SkillPromotionAdapter(skill_service),
                RoutingProfilePromotionAdapter(routing_profiles),
            )
        ),
        authorization_gate=approval_gate,
        platform_policy=platform_policy,
        telemetry=telemetry,
        post_promotion_evaluator=post_promotion_evaluator,
        post_promotion_recorder=post_promotion_recorder,
    )
    return SingleNodeLearningComposition(
        repository=repository,
        post_promotion_recorder=post_promotion_recorder,
        skills=skill_service,
        service=service,
        sources=LearningSourceBridge(service, research=research),
    )
