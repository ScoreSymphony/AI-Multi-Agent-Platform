from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.reference_host_campaign import (
    reference_host_campaign_profile,
)
from ai_multi_agent_platform.benchmarking.reference_host_reproducibility import (
    ReferenceHostReproducibilityAnalyzer,
)
from ai_multi_agent_platform.benchmarking.reference_host_reproducibility_cli import (
    main as reproducibility_main,
)

REPO_ROOT = Path(__file__).parents[3]
REPORT_SCHEMA = REPO_ROOT / "docs/schemas/benchmark-reference-host-reproducibility.v1.schema.json"
DOC_CAMPAIGN_SCHEMA = REPO_ROOT / "docs/schemas/benchmark-reference-host-campaign.v1.schema.json"
PACKAGED_CAMPAIGN_SCHEMA = (
    REPO_ROOT
    / "src/ai_multi_agent_platform/benchmarking/schemas"
    / "benchmark-reference-host-campaign.v1.schema.json"
)
COMMIT = "e" * 40


def _json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_campaign_template(root: Path, name: str, *, variant: int) -> Path:
    """Write minimal schema-valid evidence for analyzer/CLI contract tests.

    Real campaign execution is covered separately by
    tests/performance/evaluation/test_reference_host_campaign.py and
    tests/performance/test_reference_host_storage.py. These fixtures intentionally
    exercise only the reproducibility analyzer's persisted-evidence contract.
    """

    output_dir = root / name
    output_dir.mkdir()
    sweep_dir = output_dir / "sweep"
    sweep_dir.mkdir()

    configuration = reference_host_campaign_profile("smoke").to_dict()
    environment: dict[str, object] = {
        "system": "Linux",
        "release": "test",
        "machine": "x86_64",
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "python_major_minor": "3.12",
    }
    environment_sha = _canonical_sha256(environment)

    sweep_summary = sweep_dir / "summary.json"
    soak_report = output_dir / "soak.json"
    _write_json(sweep_summary, {"variant": variant})
    _write_json(soak_report, {"variant": variant})

    concurrency_envelope: list[dict[str, object]] = []
    for concurrency in (1, 2):
        throughput = 100.0 * concurrency + variant
        latency = 2.0 * concurrency + variant * 0.1
        concurrency_envelope.append(
            {
                "concurrency": concurrency,
                "sample_count": 1,
                "throughput_min_operations_per_second": throughput,
                "throughput_median_operations_per_second": throughput,
                "throughput_max_operations_per_second": throughput,
                "p95_latency_min_ms": latency,
                "p95_latency_median_ms": latency,
                "p95_latency_max_ms": latency,
                "duration_median_seconds": 0.1,
                "completed_operations_min": 2,
                "storage_growth_median_bytes": 10 + variant,
                "correctness_passed": True,
            }
        )

    generated_at = f"2026-09-10T12:00:0{variant}+00:00"
    envelope: dict[str, object] = {
        "schema_version": "1.0",
        "benchmark_id": "single-node.reference.operating-envelope.analysis",
        "benchmark_version": "1.0",
        "platform_version": "0.0.1",
        "platform_commit": COMMIT,
        "generated_at": generated_at,
        "deployment_profile": "single-node-reference",
        "persistence_profile": "sqlite-reference",
        "workload_distribution": "deterministic-task-lifecycle",
        "environment": environment,
        "environment_fingerprint_sha256": environment_sha,
        "sweep_configuration": {
            "operation_count_per_point": 2,
            "warmup_operations": 0,
            "timeout_seconds": 10.0,
            "concurrency_levels": [1, 2],
        },
        "sweep_sources": ["sweep/summary.json"],
        "endurance_sources": ["soak.json"],
        "concurrency_envelope": concurrency_envelope,
        "highest_verified_concurrency": 2,
        "endurance_evidence": [
            {
                "source": "soak.json",
                "duration_seconds": 0.25,
                "concurrency": 1,
                "max_operations": 4,
                "completed_operations": 4,
                "throughput_operations_per_second": 16.0 + variant,
                "p95_latency_ms": 3.0 + variant * 0.1,
                "resource_snapshot_count": 2,
                "traced_memory_growth_bytes": 100 + variant,
                "peak_rss_growth_bytes": 200 + variant,
                "open_file_descriptor_growth": 0,
                "storage_growth_bytes": 20 + variant,
                "latency_drift_ratio": 1.0 + variant * 0.01,
                "stop_reason": "max-operations",
                "correctness_passed": True,
            }
        ],
        "longest_verified_endurance_seconds": 0.25,
        "claim_semantics": "tested-envelope-only",
        "budget_status": "not-established",
        "correctness_passed": True,
    }
    envelope_path = output_dir / "operating-envelope.json"
    _write_json(envelope_path, envelope)

    campaign: dict[str, object] = {
        "schema_version": "1.0",
        "campaign_id": "single-node.reference.host-campaign",
        "campaign_version": "1.0",
        "profile": "smoke",
        "host_label": "reference-a",
        "platform_version": "0.0.1",
        "platform_commit": COMMIT,
        "started_at": generated_at,
        "completed_at": f"2026-09-10T12:00:1{variant}+00:00",
        "duration_seconds": 0.5,
        "work_dir_mode": "explicit",
        "configuration": configuration,
        "configuration_sha256": _canonical_sha256(configuration),
        "environment": environment,
        "environment_fingerprint_sha256": environment_sha,
        "sweep_summary": {
            "path": "sweep/summary.json",
            "sha256": _sha256(sweep_summary),
        },
        "soak_report": {
            "path": "soak.json",
            "sha256": _sha256(soak_report),
        },
        "operating_envelope": {
            "path": "operating-envelope.json",
            "sha256": _sha256(envelope_path),
        },
        "claim_semantics": "single-host-tested-evidence-only",
        "budget_status": "not-established",
        "correctness_passed": True,
    }
    _write_json(output_dir / "campaign.json", campaign)
    return output_dir


@pytest.fixture(scope="module")
def campaign_templates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Create fast persisted-evidence fixtures for reproducibility-only tests."""

    root = tmp_path_factory.mktemp("reference-host-reproducibility")
    return (
        _write_campaign_template(root, "template-1", variant=1),
        _write_campaign_template(root, "template-2", variant=2),
    )


def _campaign_copy(
    tmp_path: Path,
    name: str,
    template: Path,
    *,
    host_label: str | None = None,
) -> Path:
    output_dir = tmp_path / name
    shutil.copytree(template, output_dir)
    if host_label is not None:
        campaign_path = output_dir / "campaign.json"
        campaign = _json_object(campaign_path)
        campaign["host_label"] = host_label
        _write_json(campaign_path, campaign)
    return output_dir


def test_reproducibility_analyzer_observes_same_host_variability(
    tmp_path: Path,
    campaign_templates: tuple[Path, Path],
) -> None:
    first = _campaign_copy(tmp_path, "run-1", campaign_templates[0])
    second = _campaign_copy(tmp_path, "run-2", campaign_templates[1])

    report = ReferenceHostReproducibilityAnalyzer().analyze(
        campaign_dirs=(first, second),
    )
    payload = report.to_dict()
    Draft202012Validator(_json_object(REPORT_SCHEMA)).validate(payload)

    assert report.campaign_count == 2
    assert report.variability_observed is True
    assert report.comparison_status == "smoke-contract-only"
    assert report.claim_semantics == "same-host-comparable-campaigns-only"
    assert report.budget_status == "not-established"
    assert report.stability_classification == "not-performed"
    assert report.correctness_passed is True
    assert tuple(point.concurrency for point in report.concurrency_variability) == (1, 2)
    assert all(
        point.throughput_operations_per_second.sample_count == 2
        for point in report.concurrency_variability
    )
    assert report.endurance_variability.p95_latency_ms.sample_count == 2


def test_single_campaign_does_not_claim_variability(
    tmp_path: Path,
    campaign_templates: tuple[Path, Path],
) -> None:
    campaign = _campaign_copy(tmp_path, "run-1", campaign_templates[0])

    report = ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(campaign,))

    assert report.campaign_count == 1
    assert report.variability_observed is False
    assert report.comparison_status == "smoke-contract-only"
    assert report.concurrency_variability[0].throughput_operations_per_second.relative_range == 0
    assert (
        report.concurrency_variability[0].throughput_operations_per_second.coefficient_of_variation
        is None
    )


def test_reproducibility_cli_writes_schema_valid_report(
    tmp_path: Path,
    campaign_templates: tuple[Path, Path],
) -> None:
    first = _campaign_copy(tmp_path, "run-1", campaign_templates[0])
    second = _campaign_copy(tmp_path, "run-2", campaign_templates[1])
    output = tmp_path / "reproducibility.json"

    assert (
        reproducibility_main(
            [
                "--campaign-dir",
                str(first),
                "--campaign-dir",
                str(second),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    Draft202012Validator(_json_object(REPORT_SCHEMA)).validate(_json_object(output))


def test_reproducibility_rejects_different_host_labels(
    tmp_path: Path,
    campaign_templates: tuple[Path, Path],
) -> None:
    first = _campaign_copy(tmp_path, "run-1", campaign_templates[0], host_label="reference-a")
    second = _campaign_copy(tmp_path, "run-2", campaign_templates[1], host_label="reference-b")

    with pytest.raises(ValueError, match="incomparable host_label"):
        ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(first, second))


def test_reproducibility_rejects_tampered_envelope_evidence(
    tmp_path: Path,
    campaign_templates: tuple[Path, Path],
) -> None:
    campaign = _campaign_copy(tmp_path, "run-1", campaign_templates[0])
    envelope_path = campaign / "operating-envelope.json"
    envelope = _json_object(envelope_path)
    envelope["generated_at"] = "2026-09-10T00:00:00+00:00"
    _write_json(envelope_path, envelope)

    with pytest.raises(ValueError, match="operating-envelope evidence hash mismatch"):
        ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(campaign,))


def test_reproducibility_rejects_duplicate_campaign_directory(
    tmp_path: Path,
    campaign_templates: tuple[Path, Path],
) -> None:
    campaign = _campaign_copy(tmp_path, "run-1", campaign_templates[0])

    with pytest.raises(ValueError, match="campaign directories must be unique"):
        ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(campaign, campaign))


def test_packaged_campaign_schema_matches_documented_schema() -> None:
    assert _json_object(PACKAGED_CAMPAIGN_SCHEMA) == _json_object(DOC_CAMPAIGN_SCHEMA)


def test_campaign_manifest_retains_valid_envelope_digest_shape(tmp_path: Path) -> None:
    directory = tmp_path / "campaign"
    directory.mkdir()
    envelope = directory / "operating-envelope.json"
    envelope.write_text("{}\n", encoding="utf-8")

    assert len(_sha256(envelope)) == 64
