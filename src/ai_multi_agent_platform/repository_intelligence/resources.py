"""Resource/admission metadata for repository-intelligence workloads.

Repository intelligence has both cheap bounded reads and potentially heavy index refresh/rebuild
work. This module bridges declared provider requirements into the existing #14 placement contract
without inventing a second scheduler. Unknown heavy-work requirements fail closed: tiny-fixture
measurements are evidence, not permission to extrapolate production capacity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distributed.models import JobRequirements


class RepositoryIntelligenceWorkload(StrEnum):
    BOUNDED_QUERY = "bounded_query"
    INCREMENTAL_REFRESH = "incremental_refresh"
    FULL_REBUILD = "full_rebuild"


class ResourceEvidenceStatus(StrEnum):
    MEASURED = "measured"
    PROVISIONAL = "provisional"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceResourceEnvelope:
    """One workload's placement requirements plus non-authoritative measurement evidence."""

    workload: RepositoryIntelligenceWorkload
    cpu_cores_min: float | None = None
    ram_min_bytes: int | None = None
    storage_min_bytes: int | None = None
    concurrency_units: int = 1
    network_required: bool = False
    runtime: str | None = None
    os_name: str | None = None
    required_labels: tuple[str, ...] = ()
    host_pressure_sensitive: bool = True
    evidence_status: ResourceEvidenceStatus = ResourceEvidenceStatus.UNKNOWN
    evidence_ref: str | None = None
    observed_peak_rss_bytes: int | None = None
    observed_state_bytes: int | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.cpu_cores_min, "cpu_cores_min"),
            (self.ram_min_bytes, "ram_min_bytes"),
            (self.storage_min_bytes, "storage_min_bytes"),
            (self.observed_peak_rss_bytes, "observed_peak_rss_bytes"),
            (self.observed_state_bytes, "observed_state_bytes"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.concurrency_units < 1:
            raise ValueError("repository-intelligence concurrency_units must be >= 1")
        if self.evidence_ref is not None and not self.evidence_ref.strip():
            raise ValueError("resource evidence_ref must not be blank")
        if any(not value.strip() for value in self.required_labels + self.notes):
            raise ValueError("resource labels/notes must not contain blank values")

    @property
    def admission_complete(self) -> bool:
        """Whether this profile can safely drive scheduler admission without guessing."""

        return (
            self.cpu_cores_min is not None
            and self.ram_min_bytes is not None
            and self.storage_min_bytes is not None
        )

    def to_job_requirements(
        self,
        *,
        capability_refs: tuple[str, ...] = (),
        require_complete: bool | None = None,
    ) -> JobRequirements:
        """Project into #14 placement requirements; heavy jobs default to fail-closed."""

        if require_complete is None:
            require_complete = self.workload is not RepositoryIntelligenceWorkload.BOUNDED_QUERY
        if require_complete and not self.admission_complete:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "repository-intelligence workload lacks a complete resource admission envelope",
                details={
                    "workload": self.workload.value,
                    "evidence_status": self.evidence_status.value,
                    "evidence_ref": self.evidence_ref,
                },
            )
        return JobRequirements(
            capability_refs=capability_refs,
            cpu_cores_min=self.cpu_cores_min or 0.0,
            ram_min_bytes=self.ram_min_bytes or 0,
            storage_min_bytes=self.storage_min_bytes or 0,
            runtime=self.runtime,
            os_name=self.os_name,
            network_required=self.network_required,
            required_labels=self.required_labels,
            concurrency_units=self.concurrency_units,
        )


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceResourceProfile:
    provider_id: str
    envelopes: tuple[RepositoryIntelligenceResourceEnvelope, ...]
    max_concurrent_queries: int | None = None
    state_rebuildable: bool = True
    state_canonical: bool = False
    cleanup_behavior: str = "provider-owned derived state may be removed and rebuilt"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("resource profile provider_id must not be blank")
        if not self.envelopes:
            raise ValueError("resource profile requires at least one workload envelope")
        workloads = [item.workload for item in self.envelopes]
        if len(set(workloads)) != len(workloads):
            raise ValueError("resource profile contains duplicate workload envelopes")
        if self.max_concurrent_queries is not None and self.max_concurrent_queries < 1:
            raise ValueError("max_concurrent_queries must be >= 1")
        if self.state_canonical:
            raise ValueError("repository-intelligence derived state must not be canonical")
        if not self.cleanup_behavior.strip():
            raise ValueError("cleanup_behavior must not be blank")
        if any(not key.strip() or not value.strip() for key, value in self.metadata.items()):
            raise ValueError("resource profile metadata must use non-blank strings")

    def envelope(
        self, workload: RepositoryIntelligenceWorkload
    ) -> RepositoryIntelligenceResourceEnvelope:
        for item in self.envelopes:
            if item.workload is workload:
                return item
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"provider {self.provider_id!r} has no {workload.value!r} resource profile",
            provider_id=self.provider_id,
        )


__all__ = [
    "RepositoryIntelligenceResourceEnvelope",
    "RepositoryIntelligenceResourceProfile",
    "RepositoryIntelligenceWorkload",
    "ResourceEvidenceStatus",
]
