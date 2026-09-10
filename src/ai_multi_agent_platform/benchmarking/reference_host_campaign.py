"""Reference-host benchmark campaign orchestration for issue #440."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.deployment import SingleNodeConfig

from .endurance import EnduranceBenchmarkSpec, SingleNodeEnduranceHarness
from .operating_envelope import OperatingEnvelopeAnalyzer
from .sweep import SingleNodeSweepHarness

REFERENCE_HOST_CAMPAIGN_SCHEMA_VERSION = "1.0"
_CAMPAIGN_ID = "single-node.reference.host-campaign"
_CAMPAIGN_VERSION = "1.0"
_CLAIM_SEMANTICS = "single-host-tested-evidence-only"
_BUDGET_STATUS = "not-established"


@dataclass(frozen=True, slots=True)
class ReferenceHostCampaignProfile:
    """A versioned fixed workload profile for repeatable host-local evidence."""

    name: str
    concurrency_levels: tuple[int, ...]
    operations_per_level: int
    sweep_repetitions: int
    warmup_operations: int
    timeout_seconds: float
    soak_duration_seconds: float
    soak_sample_interval_seconds: float
    soak_max_operations: int
    soak_concurrency: int
    soak_seed_tasks: int
    soak_warmup_operations: int
    soak_read_weight: int
    soak_write_weight: int

    def __post_init__(self) -> None:
        if self.name not in {"release", "smoke"}:
            raise ValueError(f"unsupported reference-host campaign profile: {self.name}")
        if not self.concurrency_levels or any(level < 1 for level in self.concurrency_levels):
            raise ValueError("campaign concurrency levels must be positive")
        if len(set(self.concurrency_levels)) != len(self.concurrency_levels):
            raise ValueError("campaign concurrency levels must be unique")
        for value, label in (
            (self.operations_per_level, "operations_per_level"),
            (self.sweep_repetitions, "sweep_repetitions"),
            (self.soak_max_operations, "soak_max_operations"),
            (self.soak_concurrency, "soak_concurrency"),
            (self.soak_seed_tasks, "soak_seed_tasks"),
        ):
            if value < 1:
                raise ValueError(f"{label} must be at least 1")
        if self.warmup_operations < 0 or self.soak_warmup_operations < 0:
            raise ValueError("campaign warmup counts must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("campaign timeout_seconds must be positive")
        if self.soak_duration_seconds <= 0 or self.soak_sample_interval_seconds <= 0:
            raise ValueError("campaign soak durations must be positive")
        if self.soak_read_weight < 0 or self.soak_write_weight < 0:
            raise ValueError("campaign soak read/write weights must not be negative")
        if self.soak_read_weight + self.soak_write_weight < 1:
            raise ValueError("campaign soak requires a non-empty read/write mix")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["concurrency_levels"] = list(self.concurrency_levels)
        return payload


def reference_host_campaign_profile(name: str) -> ReferenceHostCampaignProfile:
    """Return one of the fixed, versioned reference-host workload profiles."""

    if name == "release":
        return ReferenceHostCampaignProfile(
            name="release",
            concurrency_levels=(1, 10, 50, 100),
            operations_per_level=500,
            sweep_repetitions=5,
            warmup_operations=20,
            timeout_seconds=60.0,
            soak_duration_seconds=3600.0,
            soak_sample_interval_seconds=10.0,
            soak_max_operations=20_000,
            soak_concurrency=10,
            soak_seed_tasks=100,
            soak_warmup_operations=20,
            soak_read_weight=4,
            soak_write_weight=1,
        )
    if name == "smoke":
        return ReferenceHostCampaignProfile(
            name="smoke",
            concurrency_levels=(1, 2),
            operations_per_level=2,
            sweep_repetitions=1,
            warmup_operations=0,
            timeout_seconds=10.0,
            soak_duration_seconds=0.25,
            soak_sample_interval_seconds=0.05,
            soak_max_operations=4,
            soak_concurrency=1,
            soak_seed_tasks=2,
            soak_warmup_operations=0,
            soak_read_weight=1,
            soak_write_weight=1,
        )
    raise ValueError(f"unsupported reference-host campaign profile: {name}")


@dataclass(frozen=True, slots=True)
class CampaignEvidenceFile:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceHostCampaignReport:
    schema_version: str
    campaign_id: str
    campaign_version: str
    profile: str
    host_label: str
    platform_version: str
    platform_commit: str
    started_at: str
    completed_at: str
    duration_seconds: float
    work_dir_mode: str
    configuration: ReferenceHostCampaignProfile
    configuration_sha256: str
    environment: Mapping[str, Any]
    environment_fingerprint_sha256: str
    sweep_summary: CampaignEvidenceFile
    soak_report: CampaignEvidenceFile
    operating_envelope: CampaignEvidenceFile
    claim_semantics: str
    budget_status: str
    correctness_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "campaign_version": self.campaign_version,
            "profile": self.profile,
            "host_label": self.host_label,
            "platform_version": self.platform_version,
            "platform_commit": self.platform_commit,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "work_dir_mode": self.work_dir_mode,
            "configuration": self.configuration.to_dict(),
            "configuration_sha256": self.configuration_sha256,
            "environment": dict(self.environment),
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "sweep_summary": asdict(self.sweep_summary),
            "soak_report": asdict(self.soak_report),
            "operating_envelope": asdict(self.operating_envelope),
            "claim_semantics": self.claim_semantics,
            "budget_status": self.budget_status,
            "correctness_passed": self.correctness_passed,
        }


class ReferenceHostCampaignRunner:
    """Run one complete single-host campaign using existing canonical benchmark harnesses."""

    def __init__(
        self,
        *,
        output_dir: Path,
        work_dir: Path,
        host_label: str,
        platform_commit: str,
        work_dir_mode: str,
    ) -> None:
        normalized_label = host_label.strip()
        normalized_commit = platform_commit.strip()
        if not normalized_label:
            raise ValueError("host_label must not be empty")
        if not normalized_commit or normalized_commit == "unknown":
            raise ValueError("platform_commit must identify the exact tested commit")
        if work_dir_mode not in {"explicit", "temporary"}:
            raise ValueError("work_dir_mode must be explicit or temporary")
        _require_disjoint_paths(output_dir, work_dir)
        self._output_dir = output_dir
        self._work_dir = work_dir
        self._host_label = normalized_label
        self._platform_commit = normalized_commit
        self._work_dir_mode = work_dir_mode

    async def run(self, profile: ReferenceHostCampaignProfile) -> ReferenceHostCampaignReport:
        _require_profile_contract(profile, work_dir_mode=self._work_dir_mode)
        _require_fresh_directory(self._output_dir, label="campaign output directory")
        _require_fresh_directory(self._work_dir, label="campaign work directory")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._work_dir.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()

        sweep_output = self._output_dir / "sweep"
        sweep_output.mkdir(parents=True, exist_ok=False)
        sweep_execution = await SingleNodeSweepHarness(
            self._work_dir / "sweep-data",
            platform_commit=self._platform_commit,
        ).run(
            concurrency_levels=profile.concurrency_levels,
            operation_count=profile.operations_per_level,
            warmup_operations=profile.warmup_operations,
            timeout_seconds=profile.timeout_seconds,
            repetitions=profile.sweep_repetitions,
        )
        for point, report in sweep_execution.point_reports:
            _write_json(sweep_output / point.report_file, report.to_dict())
        sweep_summary_path = sweep_output / "summary.json"
        _write_json(sweep_summary_path, sweep_execution.summary.to_dict())
        if not sweep_execution.summary.correctness_passed:
            raise RuntimeError("reference-host sweep correctness did not pass")

        soak_spec = EnduranceBenchmarkSpec(
            benchmark_id="single-node.soak.mixed",
            benchmark_version="1.0",
            scenario="soak",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            duration_seconds=profile.soak_duration_seconds,
            sample_interval_seconds=profile.soak_sample_interval_seconds,
            max_operations=profile.soak_max_operations,
            concurrency=profile.soak_concurrency,
            seed_tasks=profile.soak_seed_tasks,
            warmup_operations=profile.soak_warmup_operations,
            timeout_seconds=profile.timeout_seconds,
            read_weight=profile.soak_read_weight,
            write_weight=profile.soak_write_weight,
        )
        soak = await SingleNodeEnduranceHarness(
            SingleNodeConfig(data_dir=self._work_dir / "soak-data", secure_cookie=False),
            platform_commit=self._platform_commit,
        ).run(soak_spec)
        soak_path = self._output_dir / "soak.json"
        _write_json(soak_path, soak.to_dict())
        if not soak.correctness.passed:
            raise RuntimeError("reference-host soak correctness did not pass")

        envelope = OperatingEnvelopeAnalyzer().analyze(
            sweep_reports=(sweep_execution.summary.to_dict(),),
            endurance_reports=(soak.to_dict(),),
            sweep_sources=("sweep/summary.json",),
            endurance_sources=("soak.json",),
        )
        envelope_path = self._output_dir / "operating-envelope.json"
        _write_json(envelope_path, envelope.to_dict())

        completed_at = datetime.now(UTC).isoformat()
        duration_seconds = round(time.perf_counter() - started, 6)
        report = ReferenceHostCampaignReport(
            schema_version=REFERENCE_HOST_CAMPAIGN_SCHEMA_VERSION,
            campaign_id=_CAMPAIGN_ID,
            campaign_version=_CAMPAIGN_VERSION,
            profile=profile.name,
            host_label=self._host_label,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            work_dir_mode=self._work_dir_mode,
            configuration=profile,
            configuration_sha256=_canonical_sha256(profile.to_dict()),
            environment=envelope.environment,
            environment_fingerprint_sha256=envelope.environment_fingerprint_sha256,
            sweep_summary=CampaignEvidenceFile(
                path="sweep/summary.json",
                sha256=_file_sha256(sweep_summary_path),
            ),
            soak_report=CampaignEvidenceFile(path="soak.json", sha256=_file_sha256(soak_path)),
            operating_envelope=CampaignEvidenceFile(
                path="operating-envelope.json",
                sha256=_file_sha256(envelope_path),
            ),
            claim_semantics=_CLAIM_SEMANTICS,
            budget_status=_BUDGET_STATUS,
            correctness_passed=True,
        )
        _write_json(self._output_dir / "campaign.json", report.to_dict())
        return report


def _require_profile_contract(
    profile: ReferenceHostCampaignProfile,
    *,
    work_dir_mode: str,
) -> None:
    if profile.name != "release":
        return
    if work_dir_mode != "explicit":
        raise ValueError("release campaign requires an explicit work directory")
    if profile != reference_host_campaign_profile("release"):
        raise ValueError("release campaign profile must match the fixed documented release profile")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_fresh_directory(path: Path, *, label: str) -> None:
    if not path.exists():
        return
    if not path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    try:
        has_entries = next(path.iterdir(), None) is not None
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected: {path}") from exc
    if has_entries:
        raise ValueError(f"{label} must be empty: {path}")


def _require_disjoint_paths(output_dir: Path, work_dir: Path) -> None:
    output = output_dir.resolve()
    work = work_dir.resolve()
    if output == work or output in work.parents or work in output.parents:
        raise ValueError("campaign output_dir and work_dir must be disjoint")
