from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.restore_integrity_current import (
    _CurrentIndex,
    _validate_learning,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationRun,
    EvaluationSuite,
    VersionReference,
)
from ai_multi_agent_platform.learning import LearningReference
from ai_multi_agent_platform.learning.models import (
    LearningGatePlan,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
)
from ai_multi_agent_platform.security import RiskClassification
from ai_multi_agent_platform.skills import SkillContent, SkillProfile


class _SkillEvaluationStub:
    def __init__(
        self,
        *,
        run: EvaluationRun,
        suite: EvaluationSuite,
        target: LearningTarget,
    ) -> None:
        self.run = run
        self.suite = suite
        reference = SimpleNamespace(
            kind=target.resource_type.value,
            ref_id=target.resource_id,
            version=str(target.revision),
        )
        self.detail = SimpleNamespace(
            run=run,
            manifest=SimpleNamespace(
                evaluation_run_id=run.run_id,
                suite_id=run.suite_id,
                suite_version=run.suite_version,
                configuration_references=(reference,),
            ),
        )

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        assert run_id == self.run.run_id
        return self.detail

    def get_suite(self, suite_ref: str) -> EvaluationSuite:
        assert suite_ref == f"{self.suite.suite_id}@{self.suite.version}"
        return self.suite


def test_restore_learning_scope_uses_identity_bound_skill_target_fallback(tmp_path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "learning-skill-restore", secure_cookie=False)
    )
    project = deployment.scopes.create_project(
        key="learning-skill-restore",
        name="Learning Skill restore",
        owner_type="user",
        owner_id="owner-skill",
    )
    skill = deployment.learning.skills.create_skill(
        SkillProfile(
            name="Restore scoped Skill",
            purpose_categories=("test",),
            content=SkillContent(content="Preserve exact project scope after restore."),
        ),
        owner_ref=OwnerRef(type="user", id="owner-skill"),
        project_id=project.id,
    )
    target = LearningTarget(
        resource_type=LearningTargetType.SKILL,
        resource_id=skill.skill_id,
        revision=skill.revision,
    )
    candidate, created = deployment.learning.service.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="restore must preserve valid Skill evaluation scope",
        target=target,
        improvement_type="skill_content",
        expected_benefit="avoid false restore rejection",
        risk=RiskClassification.STANDARD,
        gate_plan=LearningGatePlan(
            policy_id="restore-skill-scope",
            policy_version=1,
            evaluation_suite_refs=("restore.skill.scope@1",),
        ),
        creator_ref="user:seed",
        source_refs=(LearningReference(kind="restore-test", resource_id="skill-scope"),),
        proposed_change={"content": "Preserve exact project scope after restore."},
        project_id=project.id,
    )
    assert created is True

    suite = EvaluationSuite(
        suite_id="restore.skill.scope",
        name="Restore Skill scope",
        version="1",
        cases=(
            EvaluationCase(
                case_id="skill-scope",
                name="Skill scope",
                version="1",
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
                    kind=target.resource_type.value,
                    ref_id=target.resource_id,
                    version=str(target.revision),
                ),
            ),
        ),
    )
    evaluation = _SkillEvaluationStub(run=run, suite=suite, target=target)
    cast(Any, deployment.learning.service.quality_gate).evaluation = evaluation

    evaluating = deployment.learning.service.record_gate_evidence(
        candidate.learning_candidate_id,
        evaluation_run_ids=(run.run_id,),
    )
    assert evaluating.project_id == project.id
    assert deployment.learning.service.evaluation_run_project_id(run.run_id) is None

    index = _CurrentIndex(
        task_ids=frozenset(),
        run_owner={},
        project_ids=frozenset({project.id}),
        workspace_projects={},
        file_ids=frozenset(),
        artifact_ids=frozenset(),
        result_ids=frozenset(),
        user_ids=frozenset(),
        automation_ids=frozenset(),
        verification_ids=frozenset(),
        evaluation_run_ids=frozenset({run.run_id}),
        event_ids=frozenset(),
    )

    assert _validate_learning(deployment, index) == (0, 1, 0)
