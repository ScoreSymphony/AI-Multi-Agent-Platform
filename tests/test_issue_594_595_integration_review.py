from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationSuite,
)
from ai_multi_agent_platform.evaluation.routing_profile_snapshot import (
    RoutingProfileAwareEvaluationTargetSnapshotEnricher,
)
from ai_multi_agent_platform.learning import FeedbackType, LearningReference
from ai_multi_agent_platform.learning.models import (
    LearningGatePlan,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
)
from ai_multi_agent_platform.learning.promotion import AgentPromotionAdapter
from ai_multi_agent_platform.learning.runtime import (
    PostPromotionEvaluationOutcome,
    PostPromotionEvaluationRecord,
)
from ai_multi_agent_platform.learning.scoped_control_plane import (
    ScopedLearningPostPromotionResourceService,
)
from ai_multi_agent_platform.models import new_model_routing_profile_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
    RiskClassification,
)


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


def test_learning_control_plane_enforces_record_project_scope(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "scoped-learning", secure_cookie=False)
        )
        project_a = deployment.scopes.create_project(
            key="learning-scope-a",
            name="Learning scope A",
            owner_type="user",
            owner_id="owner-a",
        )
        project_b = deployment.scopes.create_project(
            key="learning-scope-b",
            name="Learning scope B",
            owner_type="user",
            owner_id="owner-b",
        )

        feedback_a, _ = deployment.learning.service.record_feedback(
            feedback_type=FeedbackType.COMMENT,
            subject=LearningReference(kind="scope-test", resource_id="feedback-a"),
            creator_ref="user:seed",
            comment="project A",
            project_id=project_a.id,
        )
        feedback_b, _ = deployment.learning.service.record_feedback(
            feedback_type=FeedbackType.COMMENT,
            subject=LearningReference(kind="scope-test", resource_id="feedback-b"),
            creator_ref="user:seed",
            comment="project B",
            project_id=project_b.id,
        )
        global_feedback, _ = deployment.learning.service.record_feedback(
            feedback_type=FeedbackType.COMMENT,
            subject=LearningReference(kind="scope-test", resource_id="feedback-global"),
            creator_ref="user:seed",
            comment="global",
        )

        candidate_a = _candidate(deployment, project_a.id, "a")
        candidate_b = _candidate(deployment, project_b.id, "b")
        post_a = PostPromotionEvaluationRecord(
            learning_candidate_id=candidate_a.learning_candidate_id,
            candidate_revision=candidate_a.revision,
            target_revision=2,
            outcome=PostPromotionEvaluationOutcome.PASSED,
        )
        post_b = PostPromotionEvaluationRecord(
            learning_candidate_id=candidate_b.learning_candidate_id,
            candidate_revision=candidate_b.revision,
            target_revision=2,
            outcome=PostPromotionEvaluationOutcome.PASSED,
        )
        deployment.learning.post_promotion_recorder.store(post_a)
        deployment.learning.post_promotion_recorder.store(post_b)

        principal = "user:learning-project-a"
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.VIEW,
                        AuthorizationAction.READ,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project_a.id}),
            )
        )
        context = RequestContext(
            request_id="learning-scope-list",
            correlation_id="learning-scope-list",
            actor=ActorContext(
                principal_ref=principal,
                actor_type=ActorType.HUMAN.value,
            ),
        )

        feedback_page = await deployment.control_plane.list_extension_resources(
            context,
            "learning-feedback",
            PageQuery(),
        )
        feedback_items = feedback_page["items"]
        assert isinstance(feedback_items, list)
        assert {item["id"] for item in feedback_items if isinstance(item, dict)} == {
            feedback_a.feedback_id
        }
        assert feedback_b.feedback_id not in {
            item["id"] for item in feedback_items if isinstance(item, dict)
        }
        assert global_feedback.feedback_id not in {
            item["id"] for item in feedback_items if isinstance(item, dict)
        }

        candidate_page = await deployment.control_plane.list_extension_resources(
            context,
            "learning-candidates",
            PageQuery(),
        )
        candidate_items = candidate_page["items"]
        assert isinstance(candidate_items, list)
        assert {item["id"] for item in candidate_items if isinstance(item, dict)} == {
            candidate_a.learning_candidate_id
        }

        post_page = await deployment.control_plane.list_extension_resources(
            context,
            "learning-post-promotion-evaluations",
            PageQuery(),
        )
        post_items = post_page["items"]
        assert isinstance(post_items, list)
        assert {item["id"] for item in post_items if isinstance(item, dict)} == {post_a.record_id}

        with pytest.raises(ContractError) as hidden_feedback:
            await deployment.control_plane.get_extension_resource(
                context,
                "learning-feedback",
                feedback_b.feedback_id,
            )
        assert hidden_feedback.value.code is ErrorCode.NOT_FOUND

        with pytest.raises(ContractError) as hidden_post:
            await deployment.control_plane.get_extension_resource(
                context,
                "learning-post-promotion-evaluations",
                post_b.record_id,
            )
        assert hidden_post.value.code is ErrorCode.NOT_FOUND

        allowed_command_context = RequestContext(
            request_id="learning-scope-command-a",
            correlation_id="learning-scope-command-a",
            idempotency_key="learning-scope-command-a",
            actor=context.actor,
        )
        created = await deployment.control_plane.execute_command(
            allowed_command_context,
            "learning.feedback.create",
            "learning-feedback",
            {
                "feedback_type": "comment",
                "subject": {"kind": "scope-test", "resource_id": "command-a"},
                "comment": "allowed",
                "project_id": project_a.id,
            },
        )
        assert created["project_id"] == project_a.id

        denied_command_context = RequestContext(
            request_id="learning-scope-command-b",
            correlation_id="learning-scope-command-b",
            idempotency_key="learning-scope-command-b",
            actor=context.actor,
        )
        with pytest.raises(ContractError) as denied_command:
            await deployment.control_plane.execute_command(
                denied_command_context,
                "learning.feedback.create",
                "learning-feedback",
                {
                    "feedback_type": "comment",
                    "subject": {"kind": "scope-test", "resource_id": "command-b"},
                    "comment": "denied",
                    "project_id": project_b.id,
                },
            )
        assert denied_command.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_learning_supported_target_scope_blocks_cross_project_proposal_and_promotion(
    tmp_path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "target-scope", secure_cookie=False)
        )
        project_a = deployment.scopes.create_project(
            key="target-scope-a",
            name="Target scope A",
            owner_type="user",
            owner_id="owner-a",
        )
        project_b = deployment.scopes.create_project(
            key="target-scope-b",
            name="Target scope B",
            owner_type="user",
            owner_id="owner-b",
        )
        target_agent = deployment.agents.create_agent(
            _agent_profile("Project B target"),
            owner_ref=OwnerRef(type="user", id="owner-b"),
            project_id=project_b.id,
        )

        principal = "user:target-project-a"
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project_a.id}),
            )
        )
        context = RequestContext(
            request_id="cross-project-proposal",
            correlation_id="cross-project-proposal",
            idempotency_key="cross-project-proposal",
            actor=ActorContext(
                principal_ref=principal,
                actor_type=ActorType.HUMAN.value,
            ),
        )
        with pytest.raises(ContractError) as denied_proposal:
            await deployment.control_plane.execute_command(
                context,
                "learning.propose",
                "learning-candidates",
                _proposal_payload(project_a.id, target_agent.agent_id),
            )
        assert denied_proposal.value.code is ErrorCode.FORBIDDEN

        candidate, _ = deployment.learning.service.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem="cross-project mutation probe",
            target=LearningTarget(
                resource_type=LearningTargetType.AGENT,
                resource_id=target_agent.agent_id,
                revision=target_agent.revision,
            ),
            improvement_type="agent_profile",
            expected_benefit="security regression coverage",
            risk=RiskClassification.STANDARD,
            gate_plan=_gate_plan(),
            creator_ref="user:seed",
            source_refs=(LearningReference(kind="scope-test", resource_id="cross-project"),),
            proposed_change={"description": "must not be applied"},
            project_id=project_a.id,
        )
        with pytest.raises(ContractError) as denied_adapter:
            await AgentPromotionAdapter(deployment.agents).promote(
                candidate,
                principal_ref=principal,
                context=OperationContext(
                    correlation_id="cross-project-adapter",
                    project_id=project_a.id,
                ),
                actor_type=ActorType.HUMAN.value,
            )
        assert denied_adapter.value.code is ErrorCode.FORBIDDEN
        assert deployment.agents.get_agent_revision(target_agent.agent_id).revision == 1

    asyncio.run(scenario())


def test_post_promotion_list_authorizes_each_record_id(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "record-id-scope", secure_cookie=False)
        )
        project = deployment.scopes.create_project(
            key="record-id-scope",
            name="Record ID scope",
            owner_type="user",
            owner_id="owner-a",
        )
        candidate = _candidate(deployment, project.id, "record-id")
        first = PostPromotionEvaluationRecord(
            learning_candidate_id=candidate.learning_candidate_id,
            candidate_revision=candidate.revision,
            target_revision=2,
            outcome=PostPromotionEvaluationOutcome.PASSED,
        )
        second = PostPromotionEvaluationRecord(
            learning_candidate_id=candidate.learning_candidate_id,
            candidate_revision=candidate.revision,
            target_revision=3,
            outcome=PostPromotionEvaluationOutcome.PASSED,
        )
        deployment.learning.post_promotion_recorder.store(first)
        deployment.learning.post_promotion_recorder.store(second)
        access = _RecordingLearningAccess()
        service = ScopedLearningPostPromotionResourceService(deployment.learning.service, access)
        context = RequestContext(
            request_id="record-id-list",
            correlation_id="record-id-list",
            actor=ActorContext(principal_ref="user:record-reader"),
        )

        resources = await service.list_resources(context, PageQuery())

        assert {item["id"] for item in resources} == {first.record_id, second.record_id}
        assert {(resource_ref, project_id) for _, resource_ref, project_id in access.calls} == {
            (first.record_id, project.id),
            (second.record_id, project.id),
        }
        assert candidate.learning_candidate_id not in {
            resource_ref for _, resource_ref, _ in access.calls
        }

    asyncio.run(scenario())


class _RecordingLearningAccess:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        project_id: str | None,
    ) -> bool:
        del context
        self.calls.append((action, resource_ref, project_id))
        return True


def _candidate(deployment, project_id: str, suffix: str):
    candidate, _ = deployment.learning.service.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem=f"scope candidate {suffix}",
        target=LearningTarget(
            resource_type=LearningTargetType.DOCUMENTATION,
            resource_id=f"documentation-{suffix}",
            revision=1,
        ),
        improvement_type="documentation",
        expected_benefit="scope regression coverage",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref="user:seed",
        source_refs=(LearningReference(kind="scope-test", resource_id=f"source-{suffix}"),),
        project_id=project_id,
    )
    return candidate


def _agent_profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Perform the assigned work."),
        ),
    )


def _gate_plan() -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="scope-test-policy",
        policy_version=1,
        evaluation_suite_refs=("single-node.reference.lifecycle@1.0",),
    )


def _proposal_payload(project_id: str, agent_id: str) -> dict[str, object]:
    return {
        "problem": "cross-project target",
        "target": {
            "resource_type": "agent",
            "resource_id": agent_id,
            "revision": 1,
        },
        "improvement_type": "agent_profile",
        "expected_benefit": "must remain project scoped",
        "risk": "standard",
        "gate_plan": {
            "policy_id": "scope-test-policy",
            "policy_version": 1,
            "evaluation_suite_refs": ["single-node.reference.lifecycle@1.0"],
        },
        "proposed_change": {"description": "must not cross project scope"},
        "project_id": project_id,
    }
