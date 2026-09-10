from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.operating_envelope_catalog import (
    OperatingEnvelopeCatalogBuilder,
)
from ai_multi_agent_platform.benchmarking.operating_envelope_catalog_cli import main


def _fingerprint(environment: dict[str, object]) -> str:
    encoded = json.dumps(
        environment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _point(concurrency: int, *, offset: float = 0.0) -> dict[str, object]:
    throughput = 100.0 * concurrency + offset
    latency = 4.0 + concurrency + offset / 100.0
    return {
        "concurrency": concurrency,
        "sample_count": 3,
        "throughput_min_operations_per_second": throughput - 5.0,
        "throughput_median_operations_per_second": throughput,
        "throughput_max_operations_per_second": throughput + 5.0,
        "p95_latency_min_ms": latency - 0.5,
        "p95_latency_median_ms": latency,
        "p95_latency_max_ms": latency + 0.5,
        "duration_median_seconds": 2.0,
        "completed_operations_min": 500,
        "storage_growth_median_bytes": 4096.0,
        "correctness_passed": True,
    }


def _envelope(
    *,
    cpu_model: str = "reference-cpu-a",
    commit: str = "release-sha",
    levels: tuple[int, ...] = (1, 10, 50, 100),
    throughput_offset: float = 0.0,
) -> dict[str, object]:
    environment: dict[str, object] = {
        "system": "Linux",
        "release": "test-release",
        "machine": "x86_64",
        "processor": cpu_model,
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "python_major_minor": "3.12",
        "cpu_count": 8,
        "cpu_model": cpu_model,
        "memory_total_bytes": 17179869184,
    }
    return {
        "schema_version": "1.0",
        "benchmark_id": "single-node.reference.operating-envelope.analysis",
        "benchmark_version": "1.0",
        "platform_version": "0.0.1",
        "platform_commit": commit,
        "generated_at": "2026-09-10T08:00:00+00:00",
        "deployment_profile": "single-node-reference",
        "persistence_profile": "sqlite-reference",
        "workload_distribution": "deterministic-task-lifecycle",
        "environment": environment,
        "environment_fingerprint_sha256": _fingerprint(environment),
        "sweep_configuration": {
            "operation_count_per_point": 500,
            "warmup_operations": 20,
            "timeout_seconds": 60.0,
            "concurrency_levels": list(levels),
        },
        "sweep_sources": ["summary.json"],
        "endurance_sources": ["soak.json"],
        "concurrency_envelope": [_point(level, offset=throughput_offset) for level in levels],
        "highest_verified_concurrency": max(levels),
        "endurance_evidence": [],
        "longest_verified_endurance_seconds": 3600.0,
        "claim_semantics": "tested-envelope-only",
        "budget_status": "not-established",
        "correctness_passed": True,
    }


def test_catalog_preserves_distinct_host_envelopes_without_global_aggregation() -> None:
    report = OperatingEnvelopeCatalogBuilder().build(
        envelopes=(
            _envelope(cpu_model="cpu-a"),
            _envelope(cpu_model="cpu-b", throughput_offset=25.0),
        ),
        labels=("reference-a", "reference-b"),
        sources=("host-a.json", "host-b.json"),
    )

    assert report.host_count == 2
    assert report.distinct_environment_count == 2
    assert report.union_concurrency_levels == (1, 10, 50, 100)
    assert report.shared_concurrency_levels == (1, 10, 50, 100)
    assert report.comparison_status == "cross-host-comparable"
    assert report.cross_host_comparison_ready is True
    assert report.claim_semantics == "per-host-tested-envelopes-only"
    assert report.cross_host_aggregation == "not-performed"
    assert report.budget_status == "not-established"
    assert report.hosts[0].label == "reference-a"
    assert report.hosts[1].label == "reference-b"
    assert report.hosts[0].environment_fingerprint_sha256 != (
        report.hosts[1].environment_fingerprint_sha256
    )
    assert report.hosts[0].concurrency_envelope[0]["concurrency"] == 1
    assert report.hosts[1].concurrency_envelope[-1]["concurrency"] == 100

    payload = report.to_dict()
    assert "throughput_operations_per_second" not in payload
    assert "highest_verified_concurrency" not in payload
    schema = json.loads(
        Path("docs/schemas/benchmark-operating-envelope-catalog.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(payload)


def test_catalog_statuses_incomplete_cross_host_evidence_without_inventing_claims() -> None:
    single = OperatingEnvelopeCatalogBuilder().build(
        envelopes=(_envelope(),),
        labels=("one",),
    )
    assert single.comparison_status == "single-host"
    assert single.cross_host_comparison_ready is False

    same_environment = OperatingEnvelopeCatalogBuilder().build(
        envelopes=(_envelope(), _envelope(throughput_offset=5.0)),
        labels=("run-a", "run-b"),
    )
    assert same_environment.comparison_status == "same-environment-only"
    assert same_environment.cross_host_comparison_ready is False

    no_overlap = OperatingEnvelopeCatalogBuilder().build(
        envelopes=(
            _envelope(cpu_model="cpu-a", levels=(1, 10)),
            _envelope(cpu_model="cpu-b", levels=(50, 100)),
        ),
        labels=("host-a", "host-b"),
    )
    assert no_overlap.union_concurrency_levels == (1, 10, 50, 100)
    assert no_overlap.shared_concurrency_levels == ()
    assert no_overlap.comparison_status == "cross-host-no-shared-concurrency"
    assert no_overlap.cross_host_comparison_ready is False


def test_catalog_allows_different_level_sets_when_they_share_comparison_points() -> None:
    report = OperatingEnvelopeCatalogBuilder().build(
        envelopes=(
            _envelope(cpu_model="cpu-a", levels=(1, 10, 50)),
            _envelope(cpu_model="cpu-b", levels=(1, 10, 100)),
        ),
        labels=("host-a", "host-b"),
    )
    assert report.union_concurrency_levels == (1, 10, 50, 100)
    assert report.shared_concurrency_levels == (1, 10)
    assert report.comparison_status == "cross-host-comparable"


def test_catalog_rejects_incomparable_basis_and_tampered_environment() -> None:
    with pytest.raises(ValueError, match="platform_commit"):
        OperatingEnvelopeCatalogBuilder().build(
            envelopes=(_envelope(), _envelope(cpu_model="cpu-b", commit="other-sha")),
            labels=("host-a", "host-b"),
        )

    tampered = _envelope(cpu_model="cpu-b")
    environment = tampered["environment"]
    assert isinstance(environment, dict)
    environment["cpu_model"] = "tampered-after-fingerprint"
    with pytest.raises(ValueError, match="environment fingerprint"):
        OperatingEnvelopeCatalogBuilder().build(
            envelopes=(_envelope(), tampered),
            labels=("host-a", "host-b"),
        )


def test_catalog_rejects_failed_evidence_and_duplicate_labels() -> None:
    failed = _envelope()
    failed["correctness_passed"] = False
    with pytest.raises(ValueError, match="correctness"):
        OperatingEnvelopeCatalogBuilder().build(
            envelopes=(failed,),
            labels=("host-a",),
        )

    with pytest.raises(ValueError, match="labels must be unique"):
        OperatingEnvelopeCatalogBuilder().build(
            envelopes=(_envelope(), _envelope(cpu_model="cpu-b")),
            labels=("same", "same"),
        )


def test_catalog_cli_writes_schema_valid_single_host_evidence(tmp_path: Path) -> None:
    envelope = tmp_path / "envelope.json"
    output = tmp_path / "catalog.json"
    envelope.write_text(json.dumps(_envelope()), encoding="utf-8")

    assert main(["--host", f"ci-reference={envelope}", "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["host_count"] == 1
    assert payload["comparison_status"] == "single-host"
    assert payload["cross_host_comparison_ready"] is False
    assert payload["cross_host_aggregation"] == "not-performed"

    schema = json.loads(
        Path("docs/schemas/benchmark-operating-envelope-catalog.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(payload)


def test_catalog_cli_rejects_invalid_host_spec_without_writing_output(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"
    assert main(["--host", "missing-path", "--output", str(output)]) == 2
    assert not output.exists()
