from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.benchmarking.inference_backend_evaluation import (
    assess_inference_backend_evaluation,
    validate_inference_backend_evaluation_report,
)
from ai_multi_agent_platform.benchmarking.inference_backend_evaluation_cli import main as cli_main

SGLANG_REVISION = "0bcd822377da7b5718e674eaf9c870d349424dd1"

CAMPAIGN = {
    "campaign_id": "issue-860-sglang-v0.5.19",
    "candidate": {
        "backend": "sglang",
        "release_commit": SGLANG_REVISION,
    },
    "contract_cases": [
        "chat-completion-non-streaming",
        "chat-completion-streaming",
        "stream-cancellation",
    ],
    "failure_cases": [
        "endpoint-unavailable-before-dispatch",
        "process-restart-readiness-recovery",
    ],
    "placement_cases": [
        "single-gpu-worker",
        "multi-gpu-single-worker",
        "remote-gpu-worker",
        "multi-node-when-environment-valid",
    ],
}

_METRICS = {
    "cold_load_seconds": 1.0,
    "ready_to_first_request_seconds": 0.1,
    "request_throughput_per_second": 10.0,
    "input_token_throughput_per_second": 100.0,
    "output_token_throughput_per_second": 50.0,
    "total_token_throughput_per_second": 150.0,
    "end_to_end_latency_p50_ms": 100.0,
    "end_to_end_latency_p95_ms": 150.0,
    "end_to_end_latency_p99_ms": 200.0,
    "ttft_p50_ms": 20.0,
    "ttft_p95_ms": 30.0,
    "ttft_p99_ms": 40.0,
    "itl_or_tpot_p50_ms": 5.0,
    "itl_or_tpot_p95_ms": 8.0,
    "itl_or_tpot_p99_ms": 10.0,
    "peak_vram_bytes": 1,
    "steady_vram_bytes": 1,
    "peak_host_ram_bytes": 1,
    "steady_host_ram_bytes": 1,
    "error_count": 0,
}


def _result(case_id: str, status: str = "pass") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": status,
        "evidence_refs": [f"evidence/{case_id}.json"],
    }


def _report(backend: str) -> dict[str, Any]:
    backend_revision = SGLANG_REVISION if backend == "sglang" else f"{backend}-revision"
    return {
        "schema_version": "1.0",
        "campaign_id": "issue-860-sglang-v0.5.19",
        "backend": backend,
        "backend_revision": backend_revision,
        "platform_commit": "a" * 40,
        "started_at": "2026-09-12T00:00:00Z",
        "completed_at": "2026-09-12T00:10:00Z",
        "model": {
            "model_id": "representative-model",
            "model_revision": "model-revision",
            "quantization_or_dtype": "bf16",
        },
        "environment": {
            "backend_image_or_package": f"{backend}:pinned",
            "launch_command": f"serve {backend}",
            "os_kernel": "linux",
            "python_version": "3.12",
            "accelerator_runtime": "cuda",
            "driver_version": "driver",
            "gpu_model": "representative-gpu",
            "gpu_count": 1,
            "gpu_vram_bytes": 1,
            "cpu_model": "representative-cpu",
            "host_ram_bytes": 1,
            "worker_ids": ["worker-gpu-1"],
            "network_topology": "single-host",
            "authentication_exposure": "loopback",
            "cache_state": "warm",
        },
        "workload": {
            "scenario_id": "fixed-corpus-concurrency-1",
            "request_count": 32,
            "warmup_policy": "one-warmup",
            "input_length_policy": "fixed",
            "output_length_policy": "fixed",
            "request_rate": None,
            "concurrency": 1,
        },
        "metrics": dict(_METRICS),
        "contract_results": [_result(case_id) for case_id in CAMPAIGN["contract_cases"]],
        "failure_results": [_result(case_id) for case_id in CAMPAIGN["failure_cases"]],
        "placement": {
            "case_id": "single-gpu-worker",
            "status": "pass",
            "worker_count": 1,
            "gpu_count": 1,
        },
        "raw_evidence": [
            {
                "path": f"evidence/{backend}/raw.jsonl",
                "sha256": "b" * 64,
            }
        ],
        "comparability": {
            "comparable": True,
            "reasons": ["same worker, model revision, workload and dtype"],
        },
        "decision_eligible": True,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_report_validator_accepts_schema_complete_measured_report() -> None:
    validate_inference_backend_evaluation_report(_report("sglang"))


def test_readiness_requires_contract_failure_and_comparable_vllm_pair() -> None:
    readiness = assess_inference_backend_evaluation(
        campaign=CAMPAIGN,
        reports=[_report("sglang"), _report("vllm")],
    )

    assert readiness.ready_for_decision is True
    assert readiness.comparable_sglang_vllm_pairs == 1
    assert readiness.blockers == ()
    assert set(readiness.missing_placement_cases) == {
        "multi-gpu-single-worker",
        "remote-gpu-worker",
        "multi-node-when-environment-valid",
    }


def test_readiness_rejects_candidate_revision_that_does_not_match_campaign() -> None:
    sglang = _report("sglang")
    sglang["backend_revision"] = "f" * 40

    with pytest.raises(ValueError, match="does not match pinned revision"):
        assess_inference_backend_evaluation(
            campaign=CAMPAIGN,
            reports=[sglang, _report("vllm")],
        )


def test_readiness_rejects_superficially_comparable_pair_with_different_workload() -> None:
    sglang = _report("sglang")
    vllm = _report("vllm")
    vllm["workload"]["scenario_id"] = "different-request-corpus"

    readiness = assess_inference_backend_evaluation(
        campaign=CAMPAIGN,
        reports=[sglang, vllm],
    )

    assert readiness.ready_for_decision is False
    assert readiness.comparable_sglang_vllm_pairs == 0
    assert "no comparable decision-eligible sglang-vLLM performance pair" in readiness.blockers


def test_readiness_rejects_pair_with_different_driver_environment() -> None:
    sglang = _report("sglang")
    vllm = _report("vllm")
    vllm["environment"]["driver_version"] = "different-driver"

    readiness = assess_inference_backend_evaluation(
        campaign=CAMPAIGN,
        reports=[sglang, vllm],
    )

    assert readiness.ready_for_decision is False
    assert readiness.comparable_sglang_vllm_pairs == 0


def test_latest_failure_result_must_not_be_failing() -> None:
    initial = _report("sglang")
    later = deepcopy(initial)
    later["started_at"] = "2026-09-12T01:00:00Z"
    later["completed_at"] = "2026-09-12T01:10:00Z"
    later["failure_results"][0]["status"] = "fail"

    readiness = assess_inference_backend_evaluation(
        campaign=CAMPAIGN,
        reports=[initial, later, _report("vllm")],
    )

    assert readiness.ready_for_decision is False
    assert CAMPAIGN["failure_cases"][0] in readiness.failed_failure_cases
    assert "mandatory sglang failure/recovery cases are failing" in readiness.blockers


def test_latest_failure_result_uses_absolute_timestamp_across_offsets() -> None:
    earlier = _report("sglang")
    earlier["started_at"] = "2026-09-12T01:50:00+02:00"
    earlier["completed_at"] = "2026-09-12T02:00:00+02:00"
    later = deepcopy(earlier)
    later["started_at"] = "2026-09-12T01:20:00+00:00"
    later["completed_at"] = "2026-09-12T01:30:00+00:00"
    later["failure_results"][0]["status"] = "fail"

    readiness = assess_inference_backend_evaluation(
        campaign=CAMPAIGN,
        reports=[earlier, later, _report("vllm")],
    )

    assert CAMPAIGN["failure_cases"][0] in readiness.failed_failure_cases


def test_report_validator_rejects_invalid_raw_evidence_hash() -> None:
    report = _report("sglang")
    report["raw_evidence"][0]["sha256"] = "not-a-sha256"

    with pytest.raises(ValueError, match="raw_evidence"):
        validate_inference_backend_evaluation_report(report)


def test_cli_writes_machine_readable_ready_result(tmp_path: Path) -> None:
    campaign_path = tmp_path / "campaign.json"
    sglang_path = tmp_path / "sglang.json"
    vllm_path = tmp_path / "vllm.json"
    output_path = tmp_path / "readiness.json"
    _write_json(campaign_path, CAMPAIGN)
    _write_json(sglang_path, _report("sglang"))
    _write_json(vllm_path, _report("vllm"))

    exit_code = cli_main(
        [
            "--campaign",
            str(campaign_path),
            "--report",
            str(sglang_path),
            "--report",
            str(vllm_path),
            "--output",
            str(output_path),
            "--require-ready",
        ]
    )

    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert result["ready_for_decision"] is True
    assert result["comparable_sglang_vllm_pairs"] == 1


def test_cli_require_ready_returns_three_for_incomplete_evidence(tmp_path: Path) -> None:
    campaign_path = tmp_path / "campaign.json"
    sglang_path = tmp_path / "sglang.json"
    output_path = tmp_path / "readiness.json"
    _write_json(campaign_path, CAMPAIGN)
    _write_json(sglang_path, _report("sglang"))

    exit_code = cli_main(
        [
            "--campaign",
            str(campaign_path),
            "--report",
            str(sglang_path),
            "--output",
            str(output_path),
            "--require-ready",
        ]
    )

    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 3
    assert result["ready_for_decision"] is False
    assert "no comparable decision-eligible sglang-vLLM performance pair" in result["blockers"]
