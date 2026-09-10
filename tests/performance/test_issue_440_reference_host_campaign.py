from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.reference_host_campaign import (
    ReferenceHostCampaignRunner,
    reference_host_campaign_profile,
)
from ai_multi_agent_platform.benchmarking.reference_host_campaign_cli import main as campaign_main

REPO_ROOT = Path(__file__).parents[2]
CAMPAIGN_SCHEMA = REPO_ROOT / "docs/schemas/benchmark-reference-host-campaign.v1.schema.json"
ENVELOPE_SCHEMA = REPO_ROOT / "docs/schemas/benchmark-operating-envelope.v1.schema.json"


def _json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


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


def test_release_profile_is_locked_to_documented_campaign_values() -> None:
    profile = reference_host_campaign_profile("release")

    assert profile.name == "release"
    assert profile.concurrency_levels == (1, 10, 50, 100)
    assert profile.operations_per_level == 500
    assert profile.sweep_repetitions == 5
    assert profile.warmup_operations == 20
    assert profile.timeout_seconds == 60.0
    assert profile.soak_duration_seconds == 3600.0
    assert profile.soak_sample_interval_seconds == 10.0
    assert profile.soak_max_operations == 20_000
    assert profile.soak_concurrency == 10
    assert profile.soak_seed_tasks == 100
    assert profile.soak_warmup_operations == 20
    assert profile.soak_read_weight == 4
    assert profile.soak_write_weight == 1


@pytest.mark.asyncio
async def test_smoke_campaign_emits_schema_valid_hashed_host_evidence(tmp_path: Path) -> None:
    output_dir = tmp_path / "evidence"
    runner = ReferenceHostCampaignRunner(
        output_dir=output_dir,
        work_dir=tmp_path / "work",
        host_label="ci-reference",
        platform_commit="deadbeef",
        work_dir_mode="explicit",
    )

    report = await runner.run(reference_host_campaign_profile("smoke"))

    campaign = _json_object(output_dir / "campaign.json")
    envelope = _json_object(output_dir / "operating-envelope.json")
    Draft202012Validator(_json_object(CAMPAIGN_SCHEMA)).validate(campaign)
    Draft202012Validator(_json_object(ENVELOPE_SCHEMA)).validate(envelope)

    assert report.correctness_passed is True
    assert campaign["profile"] == "smoke"
    assert campaign["host_label"] == "ci-reference"
    assert campaign["platform_commit"] == "deadbeef"
    assert campaign["claim_semantics"] == "single-host-tested-evidence-only"
    assert campaign["budget_status"] == "not-established"
    assert envelope["claim_semantics"] == "tested-envelope-only"
    assert envelope["budget_status"] == "not-established"
    assert campaign["environment_fingerprint_sha256"] == envelope[
        "environment_fingerprint_sha256"
    ]

    configuration = campaign["configuration"]
    assert isinstance(configuration, dict)
    assert campaign["configuration_sha256"] == _canonical_sha256(configuration)

    sweep_evidence = campaign["sweep_summary"]
    soak_evidence = campaign["soak_report"]
    envelope_evidence = campaign["operating_envelope"]
    assert isinstance(sweep_evidence, dict)
    assert isinstance(soak_evidence, dict)
    assert isinstance(envelope_evidence, dict)
    assert sweep_evidence == {
        "path": "sweep/summary.json",
        "sha256": _sha256(output_dir / "sweep/summary.json"),
    }
    assert soak_evidence == {
        "path": "soak.json",
        "sha256": _sha256(output_dir / "soak.json"),
    }
    assert envelope_evidence == {
        "path": "operating-envelope.json",
        "sha256": _sha256(output_dir / "operating-envelope.json"),
    }


@pytest.mark.asyncio
async def test_campaign_refuses_nonempty_evidence_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    (output_dir / "old.json").write_text("{}\n", encoding="utf-8")
    runner = ReferenceHostCampaignRunner(
        output_dir=output_dir,
        work_dir=tmp_path / "work",
        host_label="reference-a",
        platform_commit="deadbeef",
        work_dir_mode="explicit",
    )

    with pytest.raises(ValueError, match="campaign output directory must be empty"):
        await runner.run(reference_host_campaign_profile("smoke"))


def test_campaign_refuses_overlapping_work_and_evidence_directories(tmp_path: Path) -> None:
    output_dir = tmp_path / "evidence"

    with pytest.raises(ValueError, match="must be disjoint"):
        ReferenceHostCampaignRunner(
            output_dir=output_dir,
            work_dir=output_dir / "work",
            host_label="reference-a",
            platform_commit="deadbeef",
            work_dir_mode="explicit",
        )


@pytest.mark.asyncio
async def test_release_runner_requires_explicit_work_directory_mode(tmp_path: Path) -> None:
    runner = ReferenceHostCampaignRunner(
        output_dir=tmp_path / "evidence",
        work_dir=tmp_path / "work",
        host_label="reference-a",
        platform_commit="deadbeef",
        work_dir_mode="temporary",
    )

    with pytest.raises(ValueError, match="requires an explicit work directory"):
        await runner.run(reference_host_campaign_profile("release"))


@pytest.mark.asyncio
async def test_release_runner_rejects_mutated_release_profile(tmp_path: Path) -> None:
    runner = ReferenceHostCampaignRunner(
        output_dir=tmp_path / "evidence",
        work_dir=tmp_path / "work",
        host_label="reference-a",
        platform_commit="deadbeef",
        work_dir_mode="explicit",
    )
    mutated = replace(reference_host_campaign_profile("release"), operations_per_level=1)

    with pytest.raises(ValueError, match="must match the fixed documented release profile"):
        await runner.run(mutated)


def test_release_cli_requires_explicit_measured_work_directory(tmp_path: Path) -> None:
    assert (
        campaign_main(
            [
                "--profile",
                "release",
                "--host-label",
                "reference-a",
                "--platform-commit",
                "deadbeef",
                "--output-dir",
                str(tmp_path / "evidence"),
            ]
        )
        == 2
    )


def test_unknown_campaign_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported reference-host campaign profile"):
        reference_host_campaign_profile("nightly")
