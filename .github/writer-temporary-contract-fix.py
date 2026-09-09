from pathlib import Path
import shutil


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one target, found {count}")
    return text.replace(old, new, 1)


# Keep the normative repository schema byte-for-byte aligned with the packaged runtime schema.
# store_contract_version remains optional, preserving pre-learning v1 manifests.
shutil.copyfile(
    "src/ai_multi_agent_platform/backup/backup-manifest-v1.schema.json",
    "schemas/backup-manifest-v1.schema.json",
)

governed_path = Path("tests/test_issue_595_governed_learning.py")
text = governed_path.read_text(encoding="utf-8")

eval_start = text.index("class _EvaluationStub:")
eval_end = text.index("\n\nclass _VerificationStub:")
evaluation_stub = '''class _EvaluationStub:
    def __init__(self) -> None:
        self._details: dict[str, SimpleNamespace] = {}

    def add(
        self,
        run_id: str,
        *,
        outcome: EvaluationOutcome = EvaluationOutcome.PASSED,
        regressions: bool = False,
    ) -> SimpleNamespace:
        result = SimpleNamespace(
            result_id=f"{run_id}-result",
            outcome=outcome,
        )
        findings = (
            (
                SimpleNamespace(
                    current_result_id=result.result_id,
                    case_id="case-learning",
                ),
            )
            if regressions
            else ()
        )
        baseline_run_id = f"{run_id}-baseline"
        detail = SimpleNamespace(
            run=SimpleNamespace(
                run_id=run_id,
                suite_id="learning-suite",
                suite_version=1,
                status=EvaluationRunStatus.COMPLETED,
                baseline_run_id=baseline_run_id,
                snapshot=SimpleNamespace(references=()),
            ),
            results=(result,),
            comparison=SimpleNamespace(
                current_run_id=run_id,
                baseline_run_id=baseline_run_id,
                regressions=findings,
            ),
            manifest=SimpleNamespace(
                evaluation_run_id=run_id,
                suite_id="learning-suite",
                suite_version=1,
                configuration_references=(),
            ),
        )
        self._details[run_id] = detail
        return detail

    def bind_target(self, run_id: str, target: LearningTarget) -> None:
        detail = self._details[run_id]
        reference = SimpleNamespace(
            kind=target.resource_type.value,
            ref_id=target.resource_id,
            version=str(target.revision),
        )
        detail.run.snapshot = SimpleNamespace(references=(reference,))
        detail.manifest.configuration_references = (reference,)

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        return self._details[run_id]

    def get_suite(self, suite_ref: str) -> SimpleNamespace:
        assert suite_ref == "learning-suite@1"
        return SimpleNamespace(cases=())
'''
text = text[:eval_start] + evaluation_stub + text[eval_end:]

verify_start = text.index("class _VerificationStub:")
verify_end = text.index("\n\nclass _FailPromotionAppendRepository")
verification_stub = '''class _VerificationStub:
    def __init__(self) -> None:
        self._requests: dict[str, SimpleNamespace] = {}
        self._results: dict[str, SimpleNamespace] = {}

    def add(
        self,
        verification_id: str,
        *,
        outcome: VerificationOutcome,
        project_id: str | None = None,
        target: LearningTarget | None = None,
    ) -> tuple[SimpleNamespace, SimpleNamespace]:
        subject = SimpleNamespace(
            subject_type="verification_artifact",
            subject_id=f"{verification_id}-artifact",
            revision="1",
            digest=f"digest-{verification_id}",
        )
        producer = (
            SimpleNamespace(
                agent_id=target.resource_id,
                agent_revision=target.revision,
            )
            if target is not None and target.resource_type is LearningTargetType.AGENT
            else None
        )
        request = SimpleNamespace(
            verification_id=verification_id,
            policy_id="learning-verification",
            policy_version=1,
            project_id=project_id,
            subject=subject,
            producer=producer,
        )
        result = SimpleNamespace(
            verification_result_id=f"{verification_id}-result",
            outcome=outcome,
            subject=subject,
        )
        self._requests[verification_id] = request
        self._results[verification_id] = result
        return request, result

    def get_request(self, verification_id: str) -> SimpleNamespace:
        return self._requests[verification_id]

    def result_for(self, verification_id: str) -> SimpleNamespace | None:
        return self._results.get(verification_id)
'''
text = text[:verify_start] + verification_stub + text[verify_end:]

text = replace_once(
    text,
    '''    assert created is True
    evaluating = learning.record_gate_evidence(
''',
    '''    assert created is True
    evaluation = learning.quality_gate.evaluation
    assert isinstance(evaluation, _EvaluationStub)
    evaluation.bind_target(run_id, target)
    evaluating = learning.record_gate_evidence(
''',
    "bind proposal evaluation target",
)

text = replace_once(
    text,
    '''def test_verification_finding_creates_candidate(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    verification = _VerificationStub()
    verification.add("verify-595", outcome=VerificationOutcome.NEEDS_CHANGES)
    learning = _service(
''',
    '''def test_verification_finding_creates_candidate(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    verification = _VerificationStub()
    target = _target()
    verification.add(
        "verify-595",
        outcome=VerificationOutcome.NEEDS_CHANGES,
        target=target,
    )
    learning = _service(
''',
    "verification candidate target setup",
)
text = replace_once(
    text,
    '''        target=_target(),
        improvement_type="verification_fix",
''',
    '''        target=target,
        improvement_type="verification_fix",
''',
    "verification candidate target use",
)

text = replace_once(
    text,
    '''def test_evaluation_regression_creates_candidate(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    detail = evaluation.add("eval-regression", regressions=True)
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)

    candidate, created = learning.create_from_evaluation_regression(
''',
    '''def test_evaluation_regression_creates_candidate(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    target = _target()
    detail = evaluation.add("eval-regression", regressions=True)
    evaluation.bind_target("eval-regression", target)
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)

    candidate, created = learning.create_from_evaluation_regression(
''',
    "evaluation regression target setup",
)
text = replace_once(
    text,
    '''        target=_target(),
        improvement_type="regression_fix",
''',
    '''        target=target,
        improvement_type="regression_fix",
''',
    "evaluation regression target use",
)

text = replace_once(
    text,
    '''def test_failed_evaluation_blocks_acceptance_and_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-fail", outcome=EvaluationOutcome.FAILED)
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)
    candidate, _ = learning.create_candidate(
''',
    '''def test_failed_evaluation_blocks_acceptance_and_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    target = _target()
    evaluation.add("eval-fail", outcome=EvaluationOutcome.FAILED)
    evaluation.bind_target("eval-fail", target)
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)
    candidate, _ = learning.create_candidate(
''',
    "failed evaluation target setup",
)
text = replace_once(
    text,
    '''        problem="Candidate must prove the proposed method.",
        target=_target(),
        improvement_type="method_revision",
''',
    '''        problem="Candidate must prove the proposed method.",
        target=target,
        improvement_type="method_revision",
''',
    "failed evaluation target use",
)

old_history = '''    evaluation = _EvaluationStub()
    regression_detail = evaluation.add("eval-source", regressions=True)
    verification = _VerificationStub()
    verification_request, verification_result = verification.add(
        "verify-source",
        outcome=VerificationOutcome.FAIL,
    )
    repository = SQLiteLearningRepository(tmp_path / "learning.db")
    learning = _service(repository, evaluation, verification=verification)
    target = _target()
'''
new_history = '''    evaluation = _EvaluationStub()
    evaluation_target = _target(resource_id=new_id("agent"))
    regression_detail = evaluation.add("eval-source", regressions=True)
    evaluation.bind_target("eval-source", evaluation_target)
    verification = _VerificationStub()
    verification_target = _target(resource_id=new_id("agent"))
    verification_request, verification_result = verification.add(
        "verify-source",
        outcome=VerificationOutcome.FAIL,
        target=verification_target,
    )
    repository = SQLiteLearningRepository(tmp_path / "learning.db")
    learning = _service(repository, evaluation, verification=verification)
    target = _target()
'''
text = replace_once(text, old_history, new_history, "historical source target setup")
text = replace_once(
    text,
    '''        target=_target(resource_id=new_id("agent")),
        improvement_type="verification_fix",
''',
    '''        target=verification_target,
        improvement_type="verification_fix",
''',
    "historical verification target use",
)
text = replace_once(
    text,
    '''        target=_target(resource_id=new_id("agent")),
        improvement_type="regression_fix",
''',
    '''        target=evaluation_target,
        improvement_type="regression_fix",
''',
    "historical evaluation target use",
)

governed_path.write_text(text, encoding="utf-8")

final_path = Path("tests/test_issue_594_595_final_review_regressions.py")
text = final_path.read_text(encoding="utf-8")
old = '''        self._details[run_id] = SimpleNamespace(
            run=SimpleNamespace(
                run_id=run_id,
                suite_id="learning-suite",
                suite_version="1",
                status=EvaluationRunStatus.COMPLETED,
                snapshot=SimpleNamespace(references=references),
            ),
            results=(result,),
            comparison=SimpleNamespace(regressions=findings),
        )
'''
new = '''        baseline_run_id = f"{run_id}-baseline"
        self._details[run_id] = SimpleNamespace(
            run=SimpleNamespace(
                run_id=run_id,
                suite_id="learning-suite",
                suite_version="1",
                status=EvaluationRunStatus.COMPLETED,
                baseline_run_id=baseline_run_id,
                snapshot=SimpleNamespace(references=references),
            ),
            results=(result,),
            comparison=SimpleNamespace(
                current_run_id=run_id,
                baseline_run_id=baseline_run_id,
                regressions=findings,
            ),
            manifest=SimpleNamespace(
                evaluation_run_id=run_id,
                suite_id="learning-suite",
                suite_version="1",
                configuration_references=references,
            ),
        )
'''
text = replace_once(text, old, new, "final-review evaluation lookup contract")
final_path.write_text(text, encoding="utf-8")
