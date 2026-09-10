from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.reference_host_regression import (
    ReferenceHostRegressionComparator,
)
from ai_multi_agent_platform.benchmarking.reference_host_regression_cli import (
    main as regression_main,
)

REPO_ROOT = Path(__file__).parents[2]
DOC_POLICY_SCHEMA = (
    REPO_ROOT / "docs/schemas/benchmark-reference-host-regression-policy.v1.schema.json"
)
DOC_REPORT_SCHEMA = (
    REPO_ROOT / "docs/schemas/benchmark-reference-host-regression-report.v1.schema.json"
)
DOC_REPRO_SCHEMA = (
    REPO_ROOT / "docs/schemas/benchmark-reference-host-reproducibility.v1.schema.json"
)
SCHEMA_DIR = REPO_ROOT / "src/ai_multi_agent_platform/benchmarking/schemas"
PACKAGED_POLICY_SCHEMA = SCHEMA_DIR / "benchmark-reference-host-regression-policy.v1.schema.json"
PACKAGED_REPORT_SCHEMA = SCHEMA_DIR / "benchmark-reference-host-regression-report.v1.schema.json"
PACKAGED_REPRO_SCHEMA = SCHEMA_DIR / "benchmark-reference-host-reproducibility.v1.schema.json"


def _metric(value: float, *, samples: int = 3) -> dict[str, object]:
    return {
        "sample_count": samples,
        "minimum": value * 0.98,
        "median": value,
        "maximum": value * 1.02,
        "relative_range": 0.04,
        "coefficient_of_variation": 0.01,
    }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reproducibility(
    *,
    commit: str,
    throughput: float = 100.0,
    latency: float = 10.0,
    samples: int = 3,
    host_label: str = "reference-linux-a",
) -> dict[str, object]:
    campaigns = [
        {
            "source": f"/evidence/{commit[:8]}/run-{index}/campaign.json",
            "campaign_sha256": _digest(f"{commit}:campaign:{index}"),
            "operating_envelope_path": (
                f"/evidence/{commit[:8]}/run-{index}/operating-envelope.json"
            ),
            "operating_envelope_sha256": _digest(f"{commit}:envelope:{index}"),
            "started_at": "2026-09-10T10:00:00+00:00",
            "completed_at": "2026-09-10T11:00:00+00:00",
        }
        for index in range(samples)
    ]
    return {
        "schema_version": "1.0",
        "benchmark_id": "single-node.reference.host-campaign.reproducibility",
        "benchmark_version": "1.0",
        "generated_at": "2026-09-10T12:00:00+00:00",
        "basis": {
            "host_label": host_label,
            "platform_version": "0.0.1",
            "platform_commit": commit,
            "profile": "release",
            "configuration_sha256": "a" * 64,
            "environment_fingerprint_sha256": "b" * 64,
            "deployment_profile": "single-node",
            "persistence_profile": "sqlite",
            "workload_distribution": "deterministic-reference",
        },
        "campaign_count": samples,
        "variability_observed": True,
        "comparison_status": "release-variability-observed",
        "claim_semantics": "same-host-comparable-campaigns-only",
        "budget_status": "not-established",
        "stability_classification": "not-performed",
        "campaigns": campaigns,
        "concurrency_variability": [
            {
                "concurrency": 10,
                "throughput_operations_per_second": _metric(throughput, samples=samples),
                "p95_latency_ms": _metric(latency, samples=samples),
                "storage_growth_bytes": _metric(1000.0, samples=samples),
            }
        ],
        "endurance_variability": {
            "throughput_operations_per_second": _metric(throughput, samples=samples),
            "p95_latency_ms": _metric(latency, samples=samples),
            "traced_memory_growth_bytes": _metric(100.0, samples=samples),
            "peak_rss_growth_bytes": _metric(200.0, samples=samples),
            "open_file_descriptor_growth": _metric(2.0, samples=samples),
            "storage_growth_bytes": _metric(1000.0, samples=samples),
            "latency_drift_ratio": _metric(0.02, samples=samples),
        },
        "correctness_passed": True,
    }


def _policy() -> dict[str, object]:
    refs = ["evidence://reference-linux-a/baseline", "evidence://reference-linux-a/candidate"]
    return {
        "schema_version": "1.0",
        "policy_id": "reference-linux-a.release-regression",
        "policy_version": "1.0",
        "justification": "Thresholds are derived from retained repeated release campaign evidence.",
        "evidence_refs": refs,
        "minimum_campaign_count": 3,
        "rules": [
            {
                "rule_id": "throughput-c10",
                "scope": "concurrency",
                "metric": "throughput_operations_per_second",
                "concurrency": 10,
                "direction": "higher-is-better",
                "minimum_metric_sample_count": 3,
                "noise_tolerance_relative": 0.05,
                "warning_relative_regression": 0.10,
                "release_blocking_relative_regression": 0.20,
                "evidence_refs": refs,
            }
        ],
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    policy = tmp_path / "policy.json"
    _write(baseline, _reproducibility(commit="1" * 40))
    _write(candidate, _reproducibility(commit="2" * 40))
    _write(policy, _policy())
    return baseline, candidate, policy


def test_reference_host_regression_classifies_relative_change(tmp_path: Path) -> None:
    baseline, candidate, policy = _inputs(tmp_path)
    _write(candidate, _reproducibility(commit="2" * 40, throughput=88.0))

    report = ReferenceHostRegressionComparator().compare(
        baseline_path=baseline,
        candidate_path=candidate,
        policy_path=policy,
    )

    assert report.overall_classification == "warning"
    assert report.rules[0].classification == "warning"
    assert report.rules[0].relative_regression == pytest.approx(0.12)
    assert report.platform_budget_status == "not-established"
    Draft202012Validator(_read(DOC_REPORT_SCHEMA)).validate(report.to_dict())


def test_reference_host_regression_classifies_blocking_and_improvement(tmp_path: Path) -> None:
    baseline, candidate, policy_path = _inputs(tmp_path)
    policy = _policy()
    rules = policy["rules"]
    assert isinstance(rules, list)
    rules.append(
        {
            "rule_id": "latency-c10",
            "scope": "concurrency",
            "metric": "p95_latency_ms",
            "concurrency": 10,
            "direction": "lower-is-better",
            "minimum_metric_sample_count": 3,
            "noise_tolerance_relative": 0.05,
            "warning_relative_regression": 0.10,
            "release_blocking_relative_regression": 0.20,
            "evidence_refs": policy["evidence_refs"],
        }
    )
    _write(policy_path, policy)
    _write(candidate, _reproducibility(commit="2" * 40, throughput=110.0, latency=13.0))

    report = ReferenceHostRegressionComparator().compare(
        baseline_path=baseline,
        candidate_path=candidate,
        policy_path=policy_path,
    )

    by_id = {rule.rule_id: rule for rule in report.rules}
    assert by_id["throughput-c10"].classification == "improvement"
    assert by_id["latency-c10"].classification == "release-blocking"
    assert report.overall_classification == "release-blocking"


def test_reference_host_regression_rejects_smoke_evidence(tmp_path: Path) -> None:
    baseline, candidate, policy = _inputs(tmp_path)
    candidate_payload = _reproducibility(commit="2" * 40)
    basis = candidate_payload["basis"]
    assert isinstance(basis, dict)
    basis["profile"] = "smoke"
    candidate_payload["comparison_status"] = "smoke-contract-only"
    _write(candidate, candidate_payload)

    with pytest.raises(ValueError, match="release-variability-observed"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy,
        )


def test_reference_host_regression_rejects_environment_drift(tmp_path: Path) -> None:
    baseline, candidate, policy = _inputs(tmp_path)
    candidate_payload = _reproducibility(commit="2" * 40, host_label="reference-linux-b")
    _write(candidate, candidate_payload)

    with pytest.raises(ValueError, match="incomparable host_label"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy,
        )


def test_reference_host_regression_rejects_reused_campaign_evidence(tmp_path: Path) -> None:
    baseline, candidate, policy = _inputs(tmp_path)
    baseline_payload = _read(baseline)
    candidate_payload = _read(candidate)
    baseline_campaigns = baseline_payload["campaigns"]
    candidate_campaigns = candidate_payload["campaigns"]
    assert isinstance(baseline_campaigns, list)
    assert isinstance(candidate_campaigns, list)
    baseline_first = baseline_campaigns[0]
    candidate_first = candidate_campaigns[0]
    assert isinstance(baseline_first, dict)
    assert isinstance(candidate_first, dict)
    candidate_first["campaign_sha256"] = baseline_first["campaign_sha256"]
    _write(candidate, candidate_payload)

    with pytest.raises(ValueError, match="disjoint campaigns"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy,
        )


def test_reference_host_regression_rejects_insufficient_campaigns(tmp_path: Path) -> None:
    baseline, candidate, policy = _inputs(tmp_path)
    _write(candidate, _reproducibility(commit="2" * 40, samples=2))

    with pytest.raises(ValueError, match="below policy minimum 3"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy,
        )


def test_reference_host_regression_rejects_noncanonical_direction(tmp_path: Path) -> None:
    baseline, candidate, policy_path = _inputs(tmp_path)
    policy = _policy()
    rules = policy["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    rule["direction"] = "lower-is-better"
    _write(policy_path, policy)

    with pytest.raises(ValueError, match="must use direction 'higher-is-better'"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy_path,
        )


def test_reference_host_regression_rejects_overlapping_thresholds(tmp_path: Path) -> None:
    baseline, candidate, policy_path = _inputs(tmp_path)
    policy = _policy()
    rules = policy["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    rule["noise_tolerance_relative"] = 0.10
    rule["warning_relative_regression"] = 0.10
    _write(policy_path, policy)

    with pytest.raises(ValueError, match="thresholds must satisfy"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy_path,
        )


def test_reference_host_regression_rejects_undeclared_rule_evidence(tmp_path: Path) -> None:
    baseline, candidate, policy_path = _inputs(tmp_path)
    policy = _policy()
    rules = policy["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    rule["evidence_refs"] = ["evidence://not-declared"]
    _write(policy_path, policy)

    with pytest.raises(ValueError, match="must be declared by the policy"):
        ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy_path,
        )


def test_reference_host_regression_cli_writes_schema_valid_report(tmp_path: Path) -> None:
    baseline, candidate, policy = _inputs(tmp_path)
    output = tmp_path / "regression.json"

    assert (
        regression_main(
            [
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--policy",
                str(policy),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    Draft202012Validator(_read(DOC_REPORT_SCHEMA)).validate(_read(output))


def test_reference_host_regression_schema_copies_match_documentation() -> None:
    assert _read(PACKAGED_POLICY_SCHEMA) == _read(DOC_POLICY_SCHEMA)
    assert _read(PACKAGED_REPORT_SCHEMA) == _read(DOC_REPORT_SCHEMA)
    assert _read(PACKAGED_REPRO_SCHEMA) == _read(DOC_REPRO_SCHEMA)
