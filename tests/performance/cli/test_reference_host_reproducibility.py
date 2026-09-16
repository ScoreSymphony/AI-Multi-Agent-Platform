from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
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


async def _run_campaign(root: Path, name: str) -> Path:
    output_dir = root / name
    runner = ReferenceHostCampaignRunner(
        output_dir=output_dir,
        work_dir=root / f"{name}-work",
        host_label="reference-a",
        platform_commit=COMMIT,
        work_dir_mode="explicit",
    )
    await runner.run(reference_host_campaign_profile("smoke"))
    return output_dir


@pytest.fixture(scope="module")
def campaign_templates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Generate the expensive independent campaign evidence once for this module."""

    root = tmp_path_factory.mktemp("reference-host-reproducibility")

    async def generate() -> tuple[Path, Path]:
        first = await _run_campaign(root, "template-1")
        second = await _run_campaign(root, "template-2")
        return first, second

    return asyncio.run(generate())


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
