"""ProjectAtlas resource evidence and deliberately conservative admission defaults.

The existing v0.4.5 pilot used a tiny deterministic repository. Its measurements are useful
comparative evidence, but they are not production capacity requirements. Heavy refresh/rebuild
placement therefore remains incomplete and fail-closed until a deployment supplies measured bounds
for representative repositories.
"""

from __future__ import annotations

from dataclasses import replace

from .projectatlas import PROJECTATLAS_PROVIDER_ID, PROJECTATLAS_RUNTIME_VERSION
from .resources import (
    RepositoryIntelligenceResourceEnvelope,
    RepositoryIntelligenceResourceProfile,
    RepositoryIntelligenceWorkload,
    ResourceEvidenceStatus,
)

PROJECTATLAS_PILOT_EVIDENCE_REF = "docs/extensions/REPOSITORY_INTELLIGENCE_PROJECTATLAS_PILOT.md"
PROJECTATLAS_PILOT_PEAK_RSS_BYTES = 19_032 * 1024
PROJECTATLAS_PILOT_STATE_BYTES = 820_744


def projectatlas_resource_profile() -> RepositoryIntelligenceResourceProfile:
    """Return measured evidence without pretending tiny-fixture values are production minima."""

    common = {
        "runtime": f"projectatlas-{PROJECTATLAS_RUNTIME_VERSION}",
        "os_name": "linux",
        "network_required": False,
        "host_pressure_sensitive": True,
        "evidence_ref": PROJECTATLAS_PILOT_EVIDENCE_REF,
        "observed_peak_rss_bytes": PROJECTATLAS_PILOT_PEAK_RSS_BYTES,
        "observed_state_bytes": PROJECTATLAS_PILOT_STATE_BYTES,
    }
    return RepositoryIntelligenceResourceProfile(
        provider_id=PROJECTATLAS_PROVIDER_ID,
        max_concurrent_queries=None,
        state_rebuildable=True,
        state_canonical=False,
        cleanup_behavior=(
            "disable provider, delete provider-owned external derived-state directory, then rebuild"
        ),
        envelopes=(
            RepositoryIntelligenceResourceEnvelope(
                workload=RepositoryIntelligenceWorkload.BOUNDED_QUERY,
                evidence_status=ResourceEvidenceStatus.MEASURED,
                notes=(
                    "query latency and peak RSS measured only on the deterministic tiny fixture",
                    "no production CPU/RAM/storage minimum is inferred from this observation",
                ),
                **common,
            ),
            RepositoryIntelligenceResourceEnvelope(
                workload=RepositoryIntelligenceWorkload.INCREMENTAL_REFRESH,
                evidence_status=ResourceEvidenceStatus.UNKNOWN,
                notes=(
                    "no representative dirty-workspace incremental campaign has been measured",
                    (
                        "scheduler admission must remain fail-closed until "
                        "deployment bounds are supplied"
                    ),
                ),
                **common,
            ),
            RepositoryIntelligenceResourceEnvelope(
                workload=RepositoryIntelligenceWorkload.FULL_REBUILD,
                evidence_status=ResourceEvidenceStatus.PROVISIONAL,
                notes=(
                    "initial tiny-fixture scan produced the recorded state/RSS evidence",
                    "tiny-fixture scan is not a valid production rebuild capacity requirement",
                    (
                        "scheduler admission must remain fail-closed until "
                        "deployment bounds are supplied"
                    ),
                ),
                **common,
            ),
        ),
        metadata={
            "cost_status": "free/local",
            "state_class": "derived_index",
            "pilot_scope": "tiny-deterministic-fixture",
        },
    )


def with_projectatlas_admission_bounds(
    profile: RepositoryIntelligenceResourceProfile,
    *,
    workload: RepositoryIntelligenceWorkload,
    cpu_cores_min: float,
    ram_min_bytes: int,
    storage_min_bytes: int,
    concurrency_units: int = 1,
    required_labels: tuple[str, ...] = (),
    evidence_ref: str,
) -> RepositoryIntelligenceResourceProfile:
    """Return a copy with deployment-owned measured admission bounds for one workload."""

    if profile.provider_id != PROJECTATLAS_PROVIDER_ID:
        raise ValueError("ProjectAtlas admission bounds require a ProjectAtlas resource profile")
    if not evidence_ref.strip():
        raise ValueError("ProjectAtlas admission evidence_ref must not be blank")
    replacement = replace(
        profile.envelope(workload),
        cpu_cores_min=cpu_cores_min,
        ram_min_bytes=ram_min_bytes,
        storage_min_bytes=storage_min_bytes,
        concurrency_units=concurrency_units,
        required_labels=required_labels,
        evidence_status=ResourceEvidenceStatus.MEASURED,
        evidence_ref=evidence_ref,
    )
    envelopes = tuple(
        replacement if item.workload is workload else item for item in profile.envelopes
    )
    return replace(profile, envelopes=envelopes)


__all__ = [
    "PROJECTATLAS_PILOT_EVIDENCE_REF",
    "PROJECTATLAS_PILOT_PEAK_RSS_BYTES",
    "PROJECTATLAS_PILOT_STATE_BYTES",
    "projectatlas_resource_profile",
    "with_projectatlas_admission_bounds",
]
