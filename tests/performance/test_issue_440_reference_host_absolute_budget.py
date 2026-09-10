from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.reference_host_absolute_budget import (
    ReferenceHostAbsoluteBudgetEvaluator,
)
from ai_multi_agent_platform.benchmarking.reference_host_absolute_budget_cli import (
    main as absolute_budget_main,
)

REPO_ROOT = Path(__file__).parents[2]
DOC_POLICY_SCHEMA = (
    REPO_ROOT / "docs/schemas/benchmark-reference-host-absolute-budget-policy.v1.schema.json"
)
DOC_REPORT_SCHEMA = (
    REPO_ROOT / "docs/schemas/benchmark-reference-host-absolute-budget-report.v1.schema.json"
)
SCHEMA_DIR = REPO_ROOT / "src/ai_multi_agent_platform/benchmarking/schemas"
PACKAGED_POLICY_SCHEMA = (
    SCHEMA_DIR / "benchmark-reference-host-absolute-budget-policy.v1.schema.json"
)
PACKAGED_REPORT_SCHEMA = (
    SCHEMA_DIR / "benchmark-reference-host-absolute-budget-report.v1.schema.json"
)


def _metric(value: float, *, samples: int = 3) -> dict[str, object]:
    return {
        "sample_count": samples,
        "minimum": value * 0.98,
        "median": value,
        "maximum": value * 1.02,
        "relative_range": 0.04,
        "coefficient_of_variation": 0.01,
    }


def _basis(*, host_label: str = "reference-linux-a") -> dict[str, object]:
    return {
        "host_label": host_label,
        "platform_version": "0.0.1",
        "platform_commit": "1" * 40,
        "profile": "release",
        "configuration_sha256": "a" * 64,
        "environment_fingerprint_sha256": "b" * 64,
        "deployment_profile": "single-node",
        "persistence_profile": "sqlite",
        "workload_distribution": "deterministic-reference",
    }


def _evidence(
    *,
    throughput: float = 100.0,
    latency: float = 10.0,
    samples: int = 3,
    host_label: str = "reference-linux-a",
) -> dict[str, object]:
    campaigns = [
        {
            "source": f"/evidence/run-{index}/campaign.json",
            "campaign_sha256": f"{index + 1:064x}",
            "operating_envelope_path": f"/evidence/run-{index}/operating-envelope.json",
            "operating_envelope_sha256": f"{index + 100:064x}",
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
        "basis": _basis(host_label=host_label),
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


def _policy(
    *,
    metric: str = "throughput_operations_per_second",
    direction: str = "higher-is-better",
    warning: float = 95.0,
    blocking: float = 90.0,
    host_label: str = "reference-linux-a",
) -> dict[str, object]:
    refs = ["evidence://reference-linux-a/repeated-release-campaigns"]
    basis = _basis(host_label=host_label)
    basis.pop("platform_version")
    basis.pop("platform_commit")
    return {
        "schema_version": "1.0",
        "policy_id": "reference-linux-a.absolute-release-budget",
        "policy_version": "1.0",
        "justification": "Boundaries are derived from retained repeated release measurements.",
        "evidence_refs": refs,
        "minimum_campaign_count": 3,
        "basis": basis,
        "rules": [
            {
                "rule_id": "primary-budget",
                "scope": "concurrency",
                "metric": metric,
                "concurrency": 10,
                "direction": direction,
                "minimum_metric_sample_count": 3,
                "warning_boundary": warning,
                "release_blocking_boundary": blocking,
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


def _inputs(
    tmp_path: Path,
    *,
    evidence_payload: dict[str, object] | None = None,
    policy_payload: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    evidence = tmp_path / "reproducibility.json"
    policy = tmp_path / "absolute-budget-policy.json"
    _write(evidence, evidence_payload or _evidence())
    _write(policy, policy_payload or _policy())
    return evidence, policy


@pytest.mark.parametrize(
    ("throughput", "expected"),
    [
        (100.0, "pass"),
        (94.0, "warning"),
        (89.0, "release-blocking"),
    ],
)
def test_absolute_budget_classifies_higher_is_better(
    tmp_path: Path,
    throughput: float,
    expected: str,
) -> None:
    evidence, policy = _inputs(tmp_path, evidence_payload=_evidence(throughput=throughput))

    report = ReferenceHostAbsoluteBudgetEvaluator().evaluate(
        evidence_path=evidence,
        policy_path=policy,
    )

    assert report.overall_classification == expected
    assert report.rules[0].classification == expected
    assert report.platform_budget_status == "not-established"
    assert report.budget_scope == "reference-environment-only"
    Draft202012Validator(_read(DOC_REPORT_SCHEMA)).validate(report.to_dict())


@pytest.mark.parametrize(
    ("latency", "expected"),
    [
        (10.0, "pass"),
        (13.0, "warning"),
        (16.0, "release-blocking"),
    ],
)
def test_absolute_budget_classifies_lower_is_better(
    tmp_path: Path,
    latency: float,
    expected: str,
) -> None:
    policy_payload = _policy(
        metric="p95_latency_ms",
        direction="lower-is-better",
        warning=12.0,
        blocking=15.0,
    )
    evidence, policy = _inputs(
        tmp_path,
        evidence_payload=_evidence(latency=latency),
        policy_payload=policy_payload,
    )

    report = ReferenceHostAbsoluteBudgetEvaluator().evaluate(
        evidence_path=evidence,
        policy_path=policy,
    )

    assert report.rules[0].classification == expected
    assert report.overall_classification == expected


@pytest.mark.parametrize(
    ("metric", "direction", "warning", "blocking", "value", "expected"),
    [
        (
            "throughput_operations_per_second",
            "higher-is-better",
            95.0,
            90.0,
            95.0,
            "warning",
        ),
        (
            "throughput_operations_per_second",
            "higher-is-better",
            95.0,
            90.0,
            90.0,
            "release-blocking",
        ),
        ("p95_latency_ms", "lower-is-better", 12.0, 15.0, 12.0, "warning"),
        ("p95_latency_ms", "lower-is-better", 12.0, 15.0, 15.0, "release-blocking"),
    ],
)
def test_absolute_budget_boundaries_are_inclusive(
    tmp_path: Path,
    metric: str,
    direction: str,
    warning: float,
    blocking: float,
    value: float,
    expected: str,
) -> None:
    evidence_payload = (
        _evidence(throughput=value)
        if metric == "throughput_operations_per_second"
        else _evidence(latency=value)
    )
    evidence, policy = _inputs(
        tmp_path,
        evidence_payload=evidence_payload,
        policy_payload=_policy(
            metric=metric,
            direction=direction,
            warning=warning,
            blocking=blocking,
        ),
    )

    report = ReferenceHostAbsoluteBudgetEvaluator().evaluate(
        evidence_path=evidence,
        policy_path=policy,
    )

    assert report.rules[0].classification == expected


def test_absolute_budget_rejects_smoke_evidence(tmp_path: Path) -> None:
    payload = _evidence()
    basis = payload["basis"]
    assert isinstance(basis, dict)
    basis["profile"] = "smoke"
    payload["comparison_status"] = "smoke-contract-only"
    evidence, policy = _inputs(tmp_path, evidence_payload=payload)

    with pytest.raises(ValueError, match="release-variability-observed"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_reference_environment_drift(tmp_path: Path) -> None:
    evidence, policy = _inputs(
        tmp_path,
        evidence_payload=_evidence(host_label="reference-linux-b"),
    )

    with pytest.raises(ValueError, match="basis mismatch for host_label"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_noncanonical_direction(tmp_path: Path) -> None:
    evidence, policy = _inputs(
        tmp_path,
        policy_payload=_policy(direction="lower-is-better"),
    )

    with pytest.raises(ValueError, match="must use direction 'higher-is-better'"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


@pytest.mark.parametrize(
    ("direction", "warning", "blocking", "message"),
    [
        (
            "higher-is-better",
            90.0,
            95.0,
            "release_blocking_boundary < warning_boundary",
        ),
        (
            "lower-is-better",
            15.0,
            12.0,
            "warning_boundary < release_blocking_boundary",
        ),
    ],
)
def test_absolute_budget_rejects_invalid_boundary_ordering(
    tmp_path: Path,
    direction: str,
    warning: float,
    blocking: float,
    message: str,
) -> None:
    metric = (
        "throughput_operations_per_second" if direction == "higher-is-better" else "p95_latency_ms"
    )
    evidence, policy = _inputs(
        tmp_path,
        policy_payload=_policy(
            metric=metric,
            direction=direction,
            warning=warning,
            blocking=blocking,
        ),
    )

    with pytest.raises(ValueError, match=message):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_insufficient_campaigns(tmp_path: Path) -> None:
    evidence, policy = _inputs(tmp_path, evidence_payload=_evidence(samples=2))

    with pytest.raises(ValueError, match="below policy minimum 3"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_claimed_campaign_count_mismatch(tmp_path: Path) -> None:
    payload = _evidence()
    campaigns = payload["campaigns"]
    assert isinstance(campaigns, list)
    campaigns.pop()
    evidence, policy = _inputs(tmp_path, evidence_payload=payload)

    with pytest.raises(ValueError, match="campaign_count 3 does not match 2 campaign entries"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_duplicate_campaign_hashes(tmp_path: Path) -> None:
    payload = _evidence()
    campaigns = payload["campaigns"]
    assert isinstance(campaigns, list)
    first = campaigns[0]
    second = campaigns[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    second["campaign_sha256"] = first["campaign_sha256"]
    evidence, policy = _inputs(tmp_path, evidence_payload=payload)

    with pytest.raises(ValueError, match="campaign_sha256 values must be unique"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_insufficient_metric_samples(tmp_path: Path) -> None:
    payload = _evidence()
    points = payload["concurrency_variability"]
    assert isinstance(points, list)
    point = points[0]
    assert isinstance(point, dict)
    point["throughput_operations_per_second"] = _metric(100.0, samples=2)
    evidence, policy = _inputs(tmp_path, evidence_payload=payload)

    with pytest.raises(ValueError, match="metric sample_count 2 is below policy minimum 3"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_rejects_undeclared_rule_evidence(tmp_path: Path) -> None:
    policy_payload = _policy()
    rules = policy_payload["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    rule["evidence_refs"] = ["evidence://not-declared"]
    evidence, policy = _inputs(tmp_path, policy_payload=policy_payload)

    with pytest.raises(ValueError, match="must be declared by the policy"):
        ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )


def test_absolute_budget_cli_writes_schema_valid_report(tmp_path: Path) -> None:
    evidence, policy = _inputs(tmp_path)
    output = tmp_path / "absolute-budget.json"

    assert (
        absolute_budget_main(
            [
                "--evidence",
                str(evidence),
                "--policy",
                str(policy),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    Draft202012Validator(_read(DOC_REPORT_SCHEMA)).validate(_read(output))


@pytest.mark.parametrize("input_name", ["evidence", "policy"])
def test_absolute_budget_cli_rejects_direct_output_alias(
    tmp_path: Path,
    input_name: str,
) -> None:
    evidence, policy = _inputs(tmp_path)
    target = evidence if input_name == "evidence" else policy
    before = target.read_bytes()

    assert (
        absolute_budget_main(
            [
                "--evidence",
                str(evidence),
                "--policy",
                str(policy),
                "--output",
                str(target),
            ]
        )
        == 2
    )
    assert target.read_bytes() == before


def test_absolute_budget_cli_rejects_symlink_output_alias(tmp_path: Path) -> None:
    evidence, policy = _inputs(tmp_path)
    output = tmp_path / "output-link.json"
    output.symlink_to(evidence)
    before = evidence.read_bytes()

    assert (
        absolute_budget_main(
            [
                "--evidence",
                str(evidence),
                "--policy",
                str(policy),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert evidence.read_bytes() == before


def test_absolute_budget_cli_rejects_hardlink_output_alias(tmp_path: Path) -> None:
    evidence, policy = _inputs(tmp_path)
    output = tmp_path / "output-hardlink.json"
    os.link(evidence, output)
    before = evidence.read_bytes()

    assert (
        absolute_budget_main(
            [
                "--evidence",
                str(evidence),
                "--policy",
                str(policy),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert evidence.read_bytes() == before


def test_absolute_budget_schema_copies_match_documentation() -> None:
    assert _read(PACKAGED_POLICY_SCHEMA) == _read(DOC_POLICY_SCHEMA)
    assert _read(PACKAGED_REPORT_SCHEMA) == _read(DOC_REPORT_SCHEMA)
