from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.backup import required_single_node_store_paths
from ai_multi_agent_platform.backup.integrity import RestoreValidationError
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.restore_integrity_current import (
    single_node_current_restore_integrity_validators,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationRun,
    EvaluationSuite,
    VersionReference,
)
from ai_multi_agent_platform.learning import FeedbackType, LearningReference
from ai_multi_agent_platform.learning.models import (
    LearningGatePlan,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
)
from ai_multi_agent_platform.learning.runtime import (
    PostPromotionEvaluationOutcome,
    PostPromotionEvaluationRecord,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
    RiskClassification,
)


def test_evaluation_evidence_uses_run_target_project_scope(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "evaluation-evidence-scope", secure_cookie=False)
        )
        project_a = deployment.scopes.create_project(
            key="evaluation-evidence-a",
            name="Evaluation evidence A",
            owner_type="user",
            owner_id="owner-a",
        )
        project_b = deployment.scopes.create_project(
            key="evaluation-evidence-b",
            name="Evaluation evidence B",
            owner_type="user",
            owner_id="owner-b",
        )
        target_agent = deployment.agents.create_agent(
            _agent_profile("Evaluation target B"),
            owner_ref=OwnerRef(type="user", id="owner-b"),
            project_id=project_b.id,
        )
        candidate, _ = deployment.learning.service.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem="evaluation scope isolation",
            target=LearningTarget(
                resource_type=LearningTargetType.DOCUMENTATION,
                resource_id="documentation-evaluation-scope",
                revision=1,
            ),
            improvement_type="documentation",
            expected_benefit="scope regression coverage",
            risk=RiskClassification.STANDARD,
            gate_plan=LearningGatePlan(
                policy_id="evaluation-scope-policy",
                policy_version=1,
                evaluation_suite_refs=("learning.evaluation.scope@1",),
            ),
            creator_ref="user:seed",
            source_refs=(
                LearningReference(kind="scope-test", resource_id="evaluation-scope-source"),
            ),
            project_id=project_a.id,
        )
        suite = EvaluationSuite(
            suite_id="learning.evaluation.scope",
            name="Learning evaluation scope",
            version="1",
            cases=(
                EvaluationCase(
                    case_id="project-b-target",
                    name="Project B target",
                    version="1",
                    input_template={
                        "evaluation_target": {
                            "kind": "agent",
                            "agent_id": target_agent.agent_id,
                            "agent_revision": target_agent.revision,
                        }
                    },
                ),
            ),
        )
        run = EvaluationRun(
            suite_id=suite.suite_id,
            suite_version=suite.version,
            snapshot=ConfigurationSnapshot(
                platform_version="test",
                references=(
                    VersionReference(
                        kind="agent",
                        ref_id=target_agent.agent_id,
                        version=str(target_agent.revision),
                    ),
                ),
            ),
        )
        evaluation = _EvaluationStub(run=run, suite=suite)
        cast(Any, deployment.learning.service.quality_gate).evaluation = evaluation

        assert deployment.learning.service.evaluation_run_project_id(run.run_id) == project_b.id
        with pytest.raises(ContractError) as direct_denied:
            deployment.learning.service.record_gate_evidence(
                candidate.learning_candidate_id,
                evaluation_run_ids=(run.run_id,),
            )
        assert direct_denied.value.code is ErrorCode.FORBIDDEN

        principal = "user:evaluation-project-a"
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project_a.id}),
            )
        )
        with pytest.raises(ContractError) as public_denied:
            await deployment.control_plane.execute_command(
                RequestContext(
                    request_id="evaluation-evidence-cross-project",
                    correlation_id="evaluation-evidence-cross-project",
                    idempotency_key="evaluation-evidence-cross-project",
                    actor=ActorContext(
                        principal_ref=principal,
                        actor_type=ActorType.HUMAN.value,
                    ),
                ),
                "learning.evidence",
                candidate.learning_candidate_id,
                {"evaluation_run_ids": [run.run_id]},
            )
        assert public_denied.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_learning_databases_are_required_backup_stores() -> None:
    required = set(required_single_node_store_paths())

    assert "db/learning.sqlite3" in required
    assert "db/learning-post-promotion.sqlite3" in required


def test_restore_validator_reconstructs_learning_and_rejects_orphan_post_promotion(
    tmp_path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "learning-restore", secure_cookie=False)
        )
        project = deployment.scopes.create_project(
            key="learning-restore",
            name="Learning restore",
            owner_type="user",
            owner_id="owner-a",
        )
        target_agent = deployment.agents.create_agent(
            _agent_profile("Restore target"),
            owner_ref=OwnerRef(type="user", id="owner-a"),
            project_id=project.id,
        )
        deployment.learning.service.record_feedback(
            feedback_type=FeedbackType.COMMENT,
            subject=LearningReference(kind="restore-test", resource_id="feedback"),
            creator_ref="user:seed",
            comment="restore reconstruction",
            project_id=project.id,
        )
        deployment.learning.service.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem="restore candidate",
            target=LearningTarget(
                resource_type=LearningTargetType.AGENT,
                resource_id=target_agent.agent_id,
                revision=target_agent.revision,
            ),
            improvement_type="agent_profile",
            expected_benefit="restore reconstruction",
            risk=RiskClassification.STANDARD,
            gate_plan=LearningGatePlan(
                policy_id="restore-policy",
                policy_version=1,
                evaluation_suite_refs=("single-node.reference.lifecycle@1.0",),
            ),
            creator_ref="user:seed",
            source_refs=(LearningReference(kind="restore-test", resource_id="candidate"),),
            proposed_change={"description": "restore candidate"},
            project_id=project.id,
        )

        validator = single_node_current_restore_integrity_validators(deployment)[0]
        await validator(())

        orphan_candidate_id = "learning_candidate_00000000-0000-4000-8000-000000000595"
        deployment.learning.post_promotion_recorder.store(
            PostPromotionEvaluationRecord(
                learning_candidate_id=orphan_candidate_id,
                candidate_revision=1,
                target_revision=2,
                outcome=PostPromotionEvaluationOutcome.FAILED,
            )
        )
        with pytest.raises(RestoreValidationError, match="references missing candidate"):
            await validator(())

    asyncio.run(scenario())


class _EvaluationStub:
    def __init__(self, *, run: EvaluationRun, suite: EvaluationSuite) -> None:
        self.run = run
        self.suite = suite

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        assert run_id == self.run.run_id
        return SimpleNamespace(run=self.run)

    def get_suite(self, suite_ref: str) -> EvaluationSuite:
        assert suite_ref == f"{self.suite.suite_id}@{self.suite.version}"
        return self.suite


def _agent_profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Perform the assigned work."),
        ),
    )
