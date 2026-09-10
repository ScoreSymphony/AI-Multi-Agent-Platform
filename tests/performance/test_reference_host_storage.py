from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.benchmarking.operating_envelope import (
    OperatingEnvelopeReport,
    SweepConfiguration,
)
from ai_multi_agent_platform.benchmarking.reference_host_campaign import (
    ReferenceHostCampaignRunner,
    reference_host_campaign_profile,
)
from ai_multi_agent_platform.benchmarking.reference_host_storage import (
    attach_storage_target,
    storage_target_metadata,
)
import ai_multi_agent_platform.benchmarking.reference_host_storage as reference_host_storage


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _envelope() -> OperatingEnvelopeReport:
    environment = {
        "system": "Linux",
        "release": "test",
        "machine": "x86_64",
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "python_major_minor": "3.12",
    }
    return OperatingEnvelopeReport(
        schema_version="1.0",
        benchmark_id="single-node.reference.operating-envelope",
        benchmark_version="1.0",
        platform_version="0.0.1",
        platform_commit="1" * 40,
        generated_at="2026-09-10T20:00:00+00:00",
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        workload_distribution="deterministic-task-lifecycle",
        environment=environment,
        environment_fingerprint_sha256=_fingerprint(environment),
        sweep_configuration=SweepConfiguration(
            operation_count_per_point=1,
            warmup_operations=0,
            timeout_seconds=1.0,
            concurrency_levels=(1,),
        ),
        sweep_sources=("sweep.json",),
        endurance_sources=(),
        concurrency_envelope=(),
        highest_verified_concurrency=1,
        endurance_evidence=(),
        longest_verified_endurance_seconds=0.0,
        claim_semantics="tested-envelope-only",
        budget_status="not-established",
        correctness_passed=True,
    )


def test_storage_target_metadata_is_stable_and_privacy_safe_on_same_mount(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_metadata = storage_target_metadata(first)
    second_metadata = storage_target_metadata(second)

    assert first_metadata == second_metadata
    assert first_metadata["identity_version"] == "1"
    assert first_metadata["identity_source"] in {
        "linux-mountinfo",
        "filesystem-stat-fallback",
    }
    assert isinstance(first_metadata["total_bytes"], int)
    assert first_metadata["total_bytes"] > 0
    fingerprint = first_metadata["mount_fingerprint_sha256"]
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    serialized = json.dumps(first_metadata, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert str(first) not in serialized
    assert str(second) not in serialized


def test_linux_storage_fingerprint_distinguishes_identical_mounts_by_device_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    mountinfo = tmp_path / "mountinfo"
    monkeypatch.setattr(reference_host_storage, "_MOUNTINFO_PATH", mountinfo)

    mount_point = str(tmp_path).replace("\\", "\\134").replace(" ", "\\040")
    mountinfo.write_text(
        f"42 1 0:101 / {mount_point} rw - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    first_metadata = storage_target_metadata(work_dir)

    mountinfo.write_text(
        f"43 1 0:202 / {mount_point} rw - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    second_metadata = storage_target_metadata(work_dir)

    assert first_metadata["identity_source"] == "linux-mountinfo"
    assert second_metadata["identity_source"] == "linux-mountinfo"
    assert first_metadata["filesystem_type"] == second_metadata["filesystem_type"] == "tmpfs"
    assert first_metadata["total_bytes"] == second_metadata["total_bytes"]
    assert (
        first_metadata["mount_fingerprint_sha256"]
        != second_metadata["mount_fingerprint_sha256"]
    )
    serialized = json.dumps(
        [first_metadata, second_metadata],
        sort_keys=True,
    )
    assert "0:101" not in serialized
    assert "0:202" not in serialized


def test_attach_storage_target_recomputes_operating_envelope_fingerprint(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    original = _envelope()

    enriched = attach_storage_target(original, work_dir=work_dir)

    assert "storage_target" not in original.environment
    assert enriched.environment["storage_target"] == storage_target_metadata(work_dir)
    assert enriched.environment_fingerprint_sha256 == _fingerprint(dict(enriched.environment))
    assert enriched.environment_fingerprint_sha256 != original.environment_fingerprint_sha256


@pytest.mark.asyncio
async def test_reference_host_campaign_binds_evidence_to_measured_storage_target(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    work_dir = tmp_path / "work"
    report = await ReferenceHostCampaignRunner(
        output_dir=output_dir,
        work_dir=work_dir,
        host_label="storage-smoke-host",
        platform_commit="1" * 40,
        work_dir_mode="explicit",
    ).run(reference_host_campaign_profile("smoke"))

    campaign = json.loads((output_dir / "campaign.json").read_text(encoding="utf-8"))
    envelope = json.loads(
        (output_dir / "operating-envelope.json").read_text(encoding="utf-8")
    )
    expected_storage = storage_target_metadata(work_dir)

    assert campaign["environment"]["storage_target"] == expected_storage
    assert envelope["environment"]["storage_target"] == expected_storage
    assert campaign["environment"] == envelope["environment"]
    assert report.environment == envelope["environment"]
    assert campaign["environment_fingerprint_sha256"] == _fingerprint(campaign["environment"])
    assert envelope["environment_fingerprint_sha256"] == _fingerprint(envelope["environment"])
    assert report.environment_fingerprint_sha256 == envelope["environment_fingerprint_sha256"]
