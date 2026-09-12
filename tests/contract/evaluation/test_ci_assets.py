from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.evaluation import (
    EvaluationOutcome,
    load_evaluation_baseline,
    load_evaluation_suite,
    load_regression_policy,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SUITE = _REPOSITORY_ROOT / "config" / "evaluation-suite.pr-deterministic.json"
_POLICY = _REPOSITORY_ROOT / "config" / "evaluation-regression.pr-deterministic.json"
_BASELINE = _REPOSITORY_ROOT / "config" / "evaluation-baseline.pr-deterministic.json"


def test_checked_in_deterministic_ci_assets_are_versioned_and_consistent() -> None:
    suite = load_evaluation_suite(_SUITE)
    policy = load_regression_policy(_POLICY)
    baseline = load_evaluation_baseline(_BASELINE, suite=suite)

    assert suite.suite_id == "suite.pr-deterministic"
    assert suite.version == "1"
    assert suite.tags == ("ci", "pr", "reference")
    assert len(suite.cases) == 1
    assert suite.cases[0].case_id == "case.reference-lifecycle"
    assert suite.cases[0].metric_rules[0].metric_name == "dispatch_attempts"
    assert suite.cases[0].metric_rules[0].threshold == 1.0

    assert policy.policy_id == "policy.pr-deterministic"
    assert policy.version == "1"
    assert {rule.rule_id for rule in policy.rules} == {
        "deterministic-pass-to-fail",
        "score-drop",
        "critical-case-failure",
        "security-case-failure",
    }

    assert baseline.run.suite_id == suite.suite_id
    assert baseline.run.suite_version == suite.version
    assert baseline.run.repetitions == 1
    assert len(baseline.results) == 2
    assert all(result.outcome is EvaluationOutcome.PASSED for result in baseline.results)
    assert {(result.case_id, result.evaluator.evaluator_id) for result in baseline.results} == {
        ("case.reference-lifecycle", "reference.deterministic"),
        ("case.reference-lifecycle", "reference.metric-threshold"),
    }


def test_evaluation_config_loader_rejects_duplicate_and_unknown_fields(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"suite_id":"suite.a","suite_id":"suite.b","name":"A","version":"1","cases":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON object key: suite_id"):
        load_evaluation_suite(duplicate)

    unknown = tmp_path / "unknown.json"
    unknown.write_text(
        json.dumps(
            {
                "suite_id": "suite.a",
                "name": "A",
                "version": "1",
                "cases": [],
                "provider_private_mode": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown fields: provider_private_mode"):
        load_evaluation_suite(unknown)
