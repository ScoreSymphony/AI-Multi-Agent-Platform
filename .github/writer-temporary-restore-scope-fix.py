from pathlib import Path


SERVICE = Path("src/ai_multi_agent_platform/learning/service.py")
RESTORE = Path("src/ai_multi_agent_platform/deployment/restore_integrity_current.py")
POST_PROMOTION = Path("src/ai_multi_agent_platform/learning/post_promotion_repository.py")
TEST = Path("tests/test_issue_594_595_restore_scope_regression.py")


service = SERVICE.read_text(encoding="utf-8")
if "def require_evaluation_project_scope(" not in service:
    expected = "def _require_evaluation_project_scope("
    if expected not in service:
        raise SystemExit("LearningService scope method target not found")
    service = service.replace(expected, "def require_evaluation_project_scope(", 1)
    service = service.replace(
        "self._require_evaluation_project_scope(",
        "self.require_evaluation_project_scope(",
    )
SERVICE.write_text(service, encoding="utf-8")

restore = RESTORE.read_text(encoding="utf-8")
old = '''        for evaluation_run_id in candidate.evaluation_run_ids:\n            try:\n                evaluation_project_id = learning.service.evaluation_run_project_id(\n                    evaluation_run_id\n                )\n            except ContractError as exc:\n                raise RestoreValidationError(\n                    f"{entity} cannot reconstruct Evaluation scope for {evaluation_run_id}"\n                ) from exc\n            if evaluation_project_id != candidate.project_id:\n                raise RestoreValidationError(\n                    f"{entity} Evaluation evidence belongs to a different project"\n                )\n'''
new = '''        for evaluation_run_id in candidate.evaluation_run_ids:\n            try:\n                learning.service.require_evaluation_project_scope(\n                    candidate,\n                    evaluation_run_id,\n                )\n            except ContractError as exc:\n                raise RestoreValidationError(\n                    f"{entity} cannot validate Evaluation scope for {evaluation_run_id}"\n                ) from exc\n'''
if new not in restore:
    if old not in restore:
        raise SystemExit("restore Learning Evaluation scope block target not found")
    restore = restore.replace(old, new, 1)
RESTORE.write_text(restore, encoding="utf-8")

post_promotion = POST_PROMOTION.read_text(encoding="utf-8")
checkpoint = '            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")\n'
if checkpoint not in post_promotion.split("    def store", 1)[0]:
    target = '''                );\n                """\n            )\n\n    def store'''
    replacement = '''                );\n                """\n            )\n            # Restore-integrity readers open immutable snapshots. Checkpoint the schema too,\n            # so a newly initialized empty recorder is fully visible without consulting its WAL.\n            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")\n\n    def store'''
    if target not in post_promotion:
        raise SystemExit("post-promotion schema initializer target not found")
    post_promotion = post_promotion.replace(target, replacement, 1)
POST_PROMOTION.write_text(post_promotion, encoding="utf-8")

TEST.write_text(
    '''from __future__ import annotations\n\nfrom types import SimpleNamespace\nfrom typing import Any, cast\n\nfrom ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment\nfrom ai_multi_agent_platform.deployment.restore_integrity_current import (\n    _CurrentIndex,\n    _validate_learning,\n)\nfrom ai_multi_agent_platform.domain import OwnerRef\nfrom ai_multi_agent_platform.evaluation.models import (\n    ConfigurationSnapshot,\n    EvaluationCase,\n    EvaluationRun,\n    EvaluationSuite,\n    VersionReference,\n)\nfrom ai_multi_agent_platform.learning import LearningReference\nfrom ai_multi_agent_platform.learning.models import (\n    LearningGatePlan,\n    LearningSourceType,\n    LearningTarget,\n    LearningTargetType,\n)\nfrom ai_multi_agent_platform.security import RiskClassification\nfrom ai_multi_agent_platform.skills import SkillContent, SkillProfile\n\n\nclass _SkillEvaluationStub:\n    def __init__(\n        self,\n        *,\n        run: EvaluationRun,\n        suite: EvaluationSuite,\n        target: LearningTarget,\n    ) -> None:\n        self.run = run\n        self.suite = suite\n        reference = SimpleNamespace(\n            kind=target.resource_type.value,\n            ref_id=target.resource_id,\n            version=str(target.revision),\n        )\n        self.detail = SimpleNamespace(\n            run=run,\n            manifest=SimpleNamespace(\n                evaluation_run_id=run.run_id,\n                suite_id=run.suite_id,\n                suite_version=run.suite_version,\n                configuration_references=(reference,),\n            ),\n        )\n\n    def get_run_detail(self, run_id: str) -> SimpleNamespace:\n        assert run_id == self.run.run_id\n        return self.detail\n\n    def get_suite(self, suite_ref: str) -> EvaluationSuite:\n        assert suite_ref == f"{self.suite.suite_id}@{self.suite.version}"\n        return self.suite\n\n\ndef test_restore_learning_scope_uses_identity_bound_skill_target_fallback(tmp_path) -> None:\n    deployment = build_single_node_deployment(\n        SingleNodeConfig(data_dir=tmp_path / "learning-skill-restore", secure_cookie=False)\n    )\n    project = deployment.scopes.create_project(\n        key="learning-skill-restore",\n        name="Learning Skill restore",\n        owner_type="user",\n        owner_id="owner-skill",\n    )\n    skill = deployment.learning.skills.create_skill(\n        SkillProfile(\n            name="Restore scoped Skill",\n            purpose_categories=("test",),\n            content=SkillContent(content="Preserve exact project scope after restore."),\n        ),\n        owner_ref=OwnerRef(type="user", id="owner-skill"),\n        project_id=project.id,\n    )\n    target = LearningTarget(\n        resource_type=LearningTargetType.SKILL,\n        resource_id=skill.skill_id,\n        revision=skill.revision,\n    )\n    candidate, created = deployment.learning.service.create_candidate(\n        source_type=LearningSourceType.OPERATOR_PROPOSAL,\n        problem="restore must preserve valid Skill evaluation scope",\n        target=target,\n        improvement_type="skill_content",\n        expected_benefit="avoid false restore rejection",\n        risk=RiskClassification.STANDARD,\n        gate_plan=LearningGatePlan(\n            policy_id="restore-skill-scope",\n            policy_version=1,\n            evaluation_suite_refs=("restore.skill.scope@1",),\n        ),\n        creator_ref="user:seed",\n        source_refs=(LearningReference(kind="restore-test", resource_id="skill-scope"),),\n        proposed_change={"content": "Preserve exact project scope after restore."},\n        project_id=project.id,\n    )\n    assert created is True\n\n    suite = EvaluationSuite(\n        suite_id="restore.skill.scope",\n        name="Restore Skill scope",\n        version="1",\n        cases=(\n            EvaluationCase(\n                case_id="skill-scope",\n                name="Skill scope",\n                version="1",\n            ),\n        ),\n    )\n    run = EvaluationRun(\n        suite_id=suite.suite_id,\n        suite_version=suite.version,\n        snapshot=ConfigurationSnapshot(\n            platform_version="test",\n            references=(\n                VersionReference(\n                    kind=target.resource_type.value,\n                    ref_id=target.resource_id,\n                    version=str(target.revision),\n                ),\n            ),\n        ),\n    )\n    evaluation = _SkillEvaluationStub(run=run, suite=suite, target=target)\n    cast(Any, deployment.learning.service.quality_gate).evaluation = evaluation\n\n    evaluating = deployment.learning.service.record_gate_evidence(\n        candidate.learning_candidate_id,\n        evaluation_run_ids=(run.run_id,),\n    )\n    assert evaluating.project_id == project.id\n    assert deployment.learning.service.evaluation_run_project_id(run.run_id) is None\n\n    index = _CurrentIndex(\n        task_ids=frozenset(),\n        run_owner={},\n        project_ids=frozenset({project.id}),\n        workspace_projects={},\n        file_ids=frozenset(),\n        artifact_ids=frozenset(),\n        result_ids=frozenset(),\n        user_ids=frozenset(),\n        automation_ids=frozenset(),\n        verification_ids=frozenset(),\n        evaluation_run_ids=frozenset({run.run_id}),\n        event_ids=frozenset(),\n    )\n\n    assert _validate_learning(deployment, index) == (0, 1, 0)\n''',
    encoding="utf-8",
)
