from __future__ import annotations

from types import SimpleNamespace

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationSuite,
)
from ai_multi_agent_platform.evaluation.routing_profile_snapshot import (
    RoutingProfileAwareEvaluationTargetSnapshotEnricher,
)
from ai_multi_agent_platform.learning import FeedbackType, LearningReference
from ai_multi_agent_platform.models import new_model_routing_profile_id


def test_public_single_node_composes_durable_learning_with_canonical_context_skills(
    tmp_path,
) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    first = build_single_node_deployment(config)

    assert first.learning.skills is first.context.skills
    assert first.learning.service.quality_gate.evaluation is first.evaluation
    assert first.learning.service.quality_gate.verification is first.verification

    feedback, created = first.learning.service.record_feedback(
        feedback_type=FeedbackType.COMMENT,
        subject=LearningReference(kind="integration_review", resource_id="issue-595"),
        creator_ref="user:integration-review",
        comment="restart persistence probe",
    )
    assert created is True

    restored = build_single_node_deployment(config)
    persisted = restored.learning.repository.get_feedback(feedback.feedback_id)

    assert persisted.feedback_id == feedback.feedback_id
    assert persisted.content_digest == feedback.content_digest
    assert restored.learning.skills is restored.context.skills


def test_eval_target_snapshot_pins_exact_routing_profile_revision() -> None:
    profile_id = new_model_routing_profile_id()
    agent_id = "agent_00000000-0000-4000-8000-000000000594"
    revision = SimpleNamespace(
        profile=SimpleNamespace(model=SimpleNamespace(routing_profile_ref=f"{profile_id}@r7"))
    )
    agents = SimpleNamespace(
        service=SimpleNamespace(get_agent_revision=lambda _agent_id, _revision: revision)
    )
    enricher = RoutingProfileAwareEvaluationTargetSnapshotEnricher(
        agents=agents,
        models=SimpleNamespace(),
    )
    enricher._base = SimpleNamespace(enrich=lambda _suite, snapshot: snapshot)
    suite = EvaluationSuite(
        suite_id="integration.routing-profile",
        name="Routing profile integration",
        version="1",
        cases=(
            EvaluationCase(
                case_id="routing-profile.case",
                name="Routing profile case",
                version="1",
                input_template={
                    "evaluation_target": {
                        "kind": "agent",
                        "agent_id": agent_id,
                        "agent_revision": 3,
                    }
                },
            ),
        ),
    )
    snapshot = ConfigurationSnapshot(platform_version="test")

    enriched = enricher.enrich(suite, snapshot)

    routing_ref = next(
        reference for reference in enriched.references if reference.kind == "model_routing_profile"
    )
    assert routing_ref.ref_id == profile_id
    assert routing_ref.version == "7"
