"""Reference-host benchmark campaign orchestration for issue #440."""

from __future__ import annotations

import hashlib
import json
import shutil
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
_CLAIM_SEMANTICS = "tested-reference-host-campaign-only"
_BUDGET_STATUS = "not-established"


@dataclass(frozen=True, slots=True)
class ReferenceHostDescriptor:
    """Operator-supplied labels that document a reference environment without redefining it."""

    label: str
    environment_class: str | None = None
    storage_profile: str | None = None
    virtualization_profile: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("host label must not be empty")
        for field_name in ("environment_class", "storage_profile", "virtualization_profile"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must not be empty when provided")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "label": self.label,
            "environment_class": self.environment_class,
            "storage_profile": self.storage_profile,
            "virtualization_profile": self.virtualization_profile,
        }


@dataclass(frozen=True, slots=True)
class ReferenceHostCampaignSpec:
    """Bounded sweep and soak configuration executed as one reference-host campaign."""

    profile: str
    concurrency_levels: tuple[int, ...]
    operation_count_per_point: int
    warmup_operations: int
    repetitions: int
    timeout_seconds: float
    soak_duration_seconds: float
    soak_sample_interval_seconds: float
    soak_max_operations: int
    soak_concurrency: int
    soak_seed_tasks: int
    soak_warmup_operations: int
    soak_read_weight: int = 4
    soak_write_weight: int = 1

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("campaign profile must not be empty")
        if not self.concurrency_levels:
            raise ValueError("at least one concurrency level is required")
        if any(level < 1 for level in self.concurrency_levels):
            raise ValueError("concurrency levels must be positive")
        if len(set(self.concurrency_levels)) != len(self.concurrency_levels):
            raise ValueError("concurrency levels must be unique")
        if self.operation_count_per_point < 1:
            raise ValueError("operation_count_per_point must be at least 1")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.soak_duration_seconds <= 0:
            raise ValueError("soak_duration_seconds must be positive")
        if self.soak_sample_interval_seconds <= 0:
            raise ValueError("soak_sample_interval_seconds must be positive")
        if self.soak_max_operations < 1:
            raise ValueError("soak_max_operations must be at least 1")
        if self.soak_concurrency < 1:
            raise ValueError("soak_concurrency must be at least 1")
        if self.soak_seed_tasks < 1:
            raise ValueError("soak_seed_tasks must be at least 1")
        if self.soak_warmup_operations < 0:
            raise ValueError("soak_warmup_operations must not be negative")
        if self.soak_read_weight < 0 or self.soak_write_weight < 0:
            raise ValueError("soak read/write weights must not be negative")
        if self.soak_read_weight + self.soak_write_weight < 1:
            raise ValueError("soak read/write mix must not be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["concurrency_levels"] = list(self.concurrency_levels)
        return payload


@dataclass(frozen=True, slots=True)
class CampaignArtifact:
    """Digest-backed retained JSON artifact from one campaign."""

    kind: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ReferenceHostCampaignReport:
    """Machine-readable manifest for one independently executed reference-host campaign."""

    schema_version: str
    campaign_id: str
    campaign_version: str
    generated_at: str
    platform_version: str
    platform_commit: str
    host: ReferenceHostDescriptor
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str
    environment: Mapping[str, Any]
    environment_fingerprint_sha256: str
    configuration: ReferenceHostCampaignSpec
    artifacts: tuple[CampaignArtifact, ...]
    operating_envelope_source: str
    highest_verified_concurrency: int
    longest_verified_endurance_seconds: float
    claim_semantics: str
    budget_status: str
    correctness_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "campaign_version": self.campaign_version,
            "generated_at": self.generated_at,
            "platform_version": self.platform_version,
            "platform_commit": self.platform_commit,
            "host": self.host.to_dict(),
            "deployment_profile": self.deployment_profile,
            "persistence_profile": self.persistence_profile,
            "workload_distribution": self.workload_distribution,
            "environment": dict(self.environment),
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "configuration": self.configuration.to_dict(),
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
            "operating_envelope_source": self.operating_envelope_source,
            "highest_verified_concurrency": self.highest_verified_concurrency,
            "longest_verified_endurance_seconds": self.longest_verified_endurance_seconds,
            "claim_semantics": self.claim_semantics,
            "budget_status": self.budget_status,
            "correctness_passed": self.correctness_passed,
        }


class ReferenceHostCampaignRunner:
    """Run and retain one bounded sweep+soak campaign on the current host."""

    def __init__(
        self,
        output_dir: Path,
        *,
        platform_commit: str,
        host: ReferenceHostDescriptor,
    ) -> None:
        normalized_commit = platform_commit.strip()
        if not normalized_commit or normalized_commit == "unknown":
            raise ValueError("reference-host campaign requires an explicit platform commit")
        self._output_dir = output_dir
        self._platform_commit = normalized_commit
        self._host = host

    async def run(self, spec: ReferenceHostCampaignSpec) -> ReferenceHostCampaignReport:
        _require_fresh_output_dir(self._output_dir)
        reports_root = self._output_dir / "reports"
        sweep_reports_root = reports_root / "sweep"
        work_root = self._output_dir / "work"
        sweep_work_root = work_root / "sweep"
        soak_work_root = work_root / "soak"
        sweep_reports_root.mkdir(parents=True, exist_ok=True)

        sweep = await SingleNodeSweepHarness(
            sweep_work_root,
            platform_commit=self._platform_commit,
        ).run(
            concurrency_levels=spec.concurrency_levels,
            operation_count=spec.operation_count_per_point,
            warmup_operations=spec.warmup_operations,
            timeout_seconds=spec.timeout_seconds,
            repetitions=spec.repetitions,
        )
        sweep_summary_path = sweep_reports_root / "summary.json"
        for point, point_report in sweep.point_reports:
            _write_json(sweep_reports_root / point.report_file, point_report.to_dict())
        _write_json(sweep_summary_path, sweep.summary.to_dict())
        if not sweep.summary.correctness_passed:
            raise ValueError("reference-host sweep correctness did not pass")

        soak_spec = EnduranceBenchmarkSpec(
            benchmark_id="single-node.soak.mixed",
            benchmark_version="1.0",
            scenario="soak",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            duration_seconds=spec.soak_duration_seconds,
            sample_interval_seconds=spec.soak_sample_interval_seconds,
            max_operations=spec.soak_max_operations,
            concurrency=spec.soak_concurrency,
            seed_tasks=spec.soak_seed_tasks,
            warmup_operations=spec.soak_warmup_operations,
            timeout_seconds=spec.timeout_seconds,
            read_weight=spec.soak_read_weight,
            write_weight=spec.soak_write_weight,
        )
        soak = await SingleNodeEnduranceHarness(
            SingleNodeConfig(data_dir=soak_work_root, secure_cookie=False),
            platform_commit=self._platform_commit,
        ).run(soak_spec)
        soak_path = reports_root / "soak.json"
        _write_json(soak_path, soak.to_dict())
        if not soak.correctness.passed:
            raise ValueError("reference-host soak correctness did not pass")

        envelope = OperatingEnvelopeAnalyzer().analyze(
            sweep_reports=(sweep.summary.to_dict(),),
            endurance_reports=(soak.to_dict(),),
            sweep_sources=("reports/sweep/summary.json",),
            endurance_sources=("reports/soak.json",),
        )
        envelope_path = reports_root / "operating-envelope.json"
        _write_json(envelope_path, envelope.to_dict())

        artifacts = tuple(
            _artifact(kind, path, root=self._output_dir)
            for kind, path in (
                *(
                    ("sweep-point", sweep_reports_root / point.report_file)
                    for point, _ in sweep.point_reports
                ),
                ("sweep-summary", sweep_summary_path),
                ("endurance", soak_path),
                ("operating-envelope", envelope_path),
            )
        )
        report = ReferenceHostCampaignReport(
            schema_version=REFERENCE_HOST_CAMPAIGN_SCHEMA_VERSION,
            campaign_id=_CAMPAIGN_ID,
            campaign_version=_CAMPAIGN_VERSION,
            generated_at=datetime.now(UTC).isoformat(),
            platform_version=__version__,
            platform_commit=self._platform_commit,
            host=self._host,
            deployment_profile=envelope.deployment_profile,
            persistence_profile=envelope.persistence_profile,
            workload_distribution=envelope.workload_distribution,
            environment=envelope.environment,
            environment_fingerprint_sha256=envelope.environment_fingerprint_sha256,
            configuration=spec,
            artifacts=artifacts,
            operating_envelope_source="reports/operating-envelope.json",
            highest_verified_concurrency=envelope.highest_verified_concurrency,
            longest_verified_endurance_seconds=envelope.longest_verified_endurance_seconds,
            claim_semantics=_CLAIM_SEMANTICS,
            budget_status=_BUDGET_STATUS,
            correctness_passed=envelope.correctness_passed,
        )
        _write_json(self._output_dir / "campaign.json", report.to_dict())
        shutil.rmtree(work_root, ignore_errors=True)
        return report


def smoke_campaign_spec() -> ReferenceHostCampaignSpec:
    """Tiny semantics-only profile suitable for PR CI."""

    return ReferenceHostCampaignSpec(
        profile="smoke",
        concurrency_levels=(1, 2),
        operation_count_per_point=2,
        warmup_operations=0,
        repetitions=1,
        timeout_seconds=10.0,
        soak_duration_seconds=0.05,
        soak_sample_interval_seconds=0.02,
        soak_max_operations=2,
        soak_concurrency=1,
        soak_seed_tasks=1,
        soak_warmup_operations=0,
    )


def release_campaign_spec() -> ReferenceHostCampaignSpec:
    """Release-sized default profile for dedicated documented reference hosts."""

    return ReferenceHostCampaignSpec(
        profile="release",
        concurrency_levels=(1, 10, 50, 100),
        operation_count_per_point=500,
        warmup_operations=20,
        repetitions=5,
        timeout_seconds=60.0,
        soak_duration_seconds=3600.0,
        soak_sample_interval_seconds=10.0,
        soak_max_operations=20_000,
        soak_concurrency=10,
        soak_seed_tasks=100,
        soak_warmup_operations=20,
    )


def _require_fresh_output_dir(root: Path) -> None:
    if not root.exists():
        return
    try:
        has_entries = next(root.iterdir(), None) is not None
    except OSError as exc:
        raise ValueError(f"campaign output directory cannot be inspected: {root}") from exc
    if has_entries:
        raise ValueError("reference-host campaign requires a fresh empty output directory")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact(kind: str, path: Path, *, root: Path) -> CampaignArtifact:
    content = path.read_bytes()
    return CampaignArtifact(
        kind=kind,
        path=path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )
