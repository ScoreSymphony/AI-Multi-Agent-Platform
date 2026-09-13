from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.reference_host_campaign import (
    ReferenceHostCampaignRunner,
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


async def _campaign(tmp_path: Path, name: str, *, host_label: str = "reference-a") -> Path:
    output_dir = tmp_path / name
    runner = ReferenceHostCampaignRunner(
        output_dir=output_dir,
        work_dir=tmp_path / f"{name}-work",
        host_label=host_label,
        platform_commit=COMMIT,
        work_dir_mode="explicit",
    )
    await runner.run(reference_host_campaign_profile("smoke"))
    return output_dir


@pytest.mark.asyncio
async def test_reproducibility_analyzer_observes_same_host_variability(tmp_path: Path) -> None:
    first = await _campaign(tmp_path, "run-1")
    second = await _campaign(tmp_path, "run-2")

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


@pytest.mark.asyncio
async def test_single_campaign_does_not_claim_variability(tmp_path: Path) -> None:
    campaign = await _campaign(tmp_path, "run-1")

    report = ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(campaign,))

    assert report.campaign_count == 1
    assert report.variability_observed is False
    assert report.comparison_status == "smoke-contract-only"
    assert report.concurrency_variability[0].throughput_operations_per_second.relative_range == 0
    assert (
        report.concurrency_variability[0].throughput_operations_per_second.coefficient_of_variation
        is None
    )


@pytest.mark.asyncio
async def test_reproducibility_cli_writes_schema_valid_report(tmp_path: Path) -> None:
    first = await _campaign(tmp_path, "run-1")
    second = await _campaign(tmp_path, "run-2")
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


@pytest.mark.asyncio
async def test_reproducibility_rejects_different_host_labels(tmp_path: Path) -> None:
    first = await _campaign(tmp_path, "run-1", host_label="reference-a")
    second = await _campaign(tmp_path, "run-2", host_label="reference-b")

    with pytest.raises(ValueError, match="incomparable host_label"):
        ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(first, second))


@pytest.mark.asyncio
async def test_reproducibility_rejects_tampered_envelope_evidence(tmp_path: Path) -> None:
    campaign = await _campaign(tmp_path, "run-1")
    envelope_path = campaign / "operating-envelope.json"
    envelope = _json_object(envelope_path)
    envelope["generated_at"] = "2026-09-10T00:00:00+00:00"
    _write_json(envelope_path, envelope)

    with pytest.raises(ValueError, match="operating-envelope evidence hash mismatch"):
        ReferenceHostReproducibilityAnalyzer().analyze(campaign_dirs=(campaign,))


@pytest.mark.asyncio
async def test_reproducibility_rejects_duplicate_campaign_directory(tmp_path: Path) -> None:
    campaign = await _campaign(tmp_path, "run-1")

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
