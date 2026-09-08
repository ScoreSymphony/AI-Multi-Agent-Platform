"""Routing-profile-aware Evaluation target snapshot enrichment for #594 integration."""

from __future__ import annotations

from ai_multi_agent_platform.agents.runtime import AgentRuntime
from ai_multi_agent_platform.models import ModelRegistry, ModelRoutingProfileRef

from .hardening import merge_snapshot_references
from .models import ConfigurationSnapshot, EvaluationSuite, VersionReference
from .product import EvaluationTargetSnapshotEnricher, parse_agent_evaluation_target


class RoutingProfileAwareEvaluationTargetSnapshotEnricher(EvaluationTargetSnapshotEnricher):
    """Pin the exact #309 routing-profile revision used by an Agent target.

    ``EvaluationTargetSnapshotEnricher`` already records Agent, prompt, model, provider and
    capability identity. Durable Agent runtime may additionally resolve an immutable
    ModelRoutingProfile revision; omitting that revision makes two materially different routes
    appear reproducibly identical. The subtype keeps the existing Evaluation service contract
    intact while closing the integrated single-node reproducibility gap.
    """

    def __init__(self, *, agents: AgentRuntime, models: ModelRegistry) -> None:
        super().__init__(agents=agents, models=models)
        self._base = EvaluationTargetSnapshotEnricher(agents=agents, models=models)

    def enrich(
        self,
        suite: EvaluationSuite,
        snapshot: ConfigurationSnapshot,
    ) -> ConfigurationSnapshot:
        enriched = self._base.enrich(suite, snapshot)
        references: list[VersionReference] = []
        for case in suite.cases:
            target = parse_agent_evaluation_target(case)
            if target is None:
                continue
            revision = self._agents.service.get_agent_revision(
                target.agent_id,
                target.agent_revision,
            )
            raw_ref = revision.profile.model.routing_profile_ref
            if raw_ref is None:
                continue
            ref = ModelRoutingProfileRef.parse(raw_ref)
            references.append(
                VersionReference(
                    kind="model_routing_profile",
                    ref_id=ref.profile_id,
                    version=str(ref.revision),
                )
            )
        return merge_snapshot_references(enriched, tuple(references))


__all__ = ["RoutingProfileAwareEvaluationTargetSnapshotEnricher"]
