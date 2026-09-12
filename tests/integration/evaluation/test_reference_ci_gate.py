from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_multi_agent_platform.evaluation import run_reference_ci_gate

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SUITE = _REPOSITORY_ROOT / "config" / "evaluation-suite.pr-deterministic.json"
_POLICY = _REPOSITORY_ROOT / "config" / "evaluation-regression.pr-deterministic.json"
_BASELINE = _REPOSITORY_ROOT / "config" / "evaluation-baseline.pr-deterministic.json"


def test_reference_ci_gate_runs_real_kernel_reference_path(tmp_path: Path) -> None:
    report = asyncio.run(
        run_reference_ci_gate(
            suite_path=_SUITE,
            policy_path=_POLICY,
            baseline_path=_BASELINE,
            workspace_root=tmp_path / "executor",
            platform_version="0.0.test",
            platform_commit="test-head",
        )
    )

    assert report.passed is True
    assert report.failed_results == ()
    assert report.regressions == ()
    assert report.summary.comparison is not None
    assert report.summary.comparison.baseline_run_id == report.baseline.run.run_id
    assert report.summary.comparison.policy_id == report.policy.policy_id
    assert report.summary.comparison.policy_version == report.policy.version
    assert report.summary.run.snapshot.platform_version == "0.0.test"
    assert report.summary.run.snapshot.platform_commit == "test-head"
    assert report.summary.run.seed == 19

    assert len(report.summary.results) == 2
    assert all(result.task_id is not None for result in report.summary.results)
    assert all(result.run_id is not None for result in report.summary.results)
    metric_result = next(
        result
        for result in report.summary.results
        if result.evaluator.evaluator_id == "reference.metric-threshold"
    )
    assert metric_result.metrics[0].metric_name == "dispatch_attempts"
    assert metric_result.metrics[0].value == 1.0
    assert metric_result.metrics[0].passed is True

    references = {
        (reference.kind, reference.ref_id): reference
        for reference in report.summary.run.snapshot.references
    }
    assert any(kind == "orchestrator" for kind, _ in references)
    assert ("executor", "reference") in references
    assert ("evaluator", "reference.deterministic") in references
    assert ("evaluator", "reference.metric-threshold") in references
    assert ("regression_policy", "policy.pr-deterministic") in references
    assert ("evaluation_suite", "suite.pr-deterministic") in references


def test_reference_ci_gate_reports_checked_in_baseline_regression(tmp_path: Path) -> None:
    suite_payload = json.loads(_SUITE.read_text(encoding="utf-8"))
    assert isinstance(suite_payload, dict)
    cases = suite_payload["cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    assertions = first_case["assertions"]
    assert isinstance(assertions, list)
    first_assertion = assertions[0]
    assert isinstance(first_assertion, dict)
    first_assertion["expected"] = "failed"

    regressed_suite = tmp_path / "regressed-suite.json"
    regressed_suite.write_text(json.dumps(suite_payload), encoding="utf-8")

    report = asyncio.run(
        run_reference_ci_gate(
            suite_path=regressed_suite,
            policy_path=_POLICY,
            baseline_path=_BASELINE,
            workspace_root=tmp_path / "executor",
            platform_version="0.0.test",
            platform_commit="regressed-head",
        )
    )

    assert report.passed is False
    assert len(report.failed_results) == 1
    assert report.failed_results[0].evaluator.evaluator_id == "reference.deterministic"
    assert {finding.rule_id for finding in report.regressions} >= {
        "deterministic-pass-to-fail",
        "score-drop",
        "critical-case-failure",
    }
    assert any(
        "task.status" in assertion.message for assertion in report.failed_results[0].assertions
    )
    assert report.diagnostics()
