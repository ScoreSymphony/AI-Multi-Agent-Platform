"""Typed contracts for Marketplace pre-mutation decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import TrustStatus


class DistributionOperation(StrEnum):
    INSTALL = "install"
    UPDATE = "update"
    UNINSTALL = "uninstall"


class DependencyStatus(StrEnum):
    SATISFIED = "satisfied"
    AVAILABLE = "available"
    OPTIONAL_MISSING = "optional_missing"
    MISSING = "missing"
    VERSION_CONFLICT = "version_conflict"
    KIND_UNKNOWN = "kind_unknown"
    KIND_CONFLICT = "kind_conflict"
    SOURCE_AMBIGUOUS = "source_ambiguous"
    SELF_DEPENDENCY = "self_dependency"
    CYCLE = "cycle"
    REQUIRED_BY_INSTALLED = "required_by_installed"
    UNKNOWN_INSTALLED_DEPENDENT = "unknown_installed_dependent"


@dataclass(frozen=True, slots=True)
class DependencyResolution:
    required_by: str
    item_id: str
    item_kind: str | None
    optional: bool
    minimum_version: str | None
    maximum_version: str | None
    status: DependencyStatus
    installed_version: str | None = None
    candidate_version: str | None = None
    candidate_kind: str | None = None
    candidate_source_registry: str | None = None
    path: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return not self.optional and self.status is not DependencyStatus.SATISFIED


@dataclass(frozen=True, slots=True)
class InstallPlanStep:
    item_id: str
    item_kind: str
    version: str
    source_registry: str | None


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    platform_compatible: bool
    operating_system_compatible: bool
    architecture_compatible: bool
    missing_runtimes: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    missing_plugins: tuple[str, ...] = ()
    missing_connectors: tuple[str, ...] = ()
    missing_models: tuple[str, ...] = ()

    @property
    def compatible(self) -> bool:
        return (
            self.platform_compatible
            and self.operating_system_compatible
            and self.architecture_compatible
            and not self.missing_runtimes
            and not self.missing_capabilities
            and not self.missing_plugins
            and not self.missing_connectors
            and not self.missing_models
        )


@dataclass(frozen=True, slots=True)
class PermissionDiff:
    previous: tuple[str, ...]
    requested: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    @property
    def escalated(self) -> bool:
        return bool(self.added)


@dataclass(frozen=True, slots=True)
class ProvenanceDiff:
    installed: bool
    previous_source_registry: str | None
    candidate_source_registry: str | None
    previous_publisher: str | None
    candidate_publisher: str
    previous_repository: str | None
    candidate_repository: str
    previous_package_reference: str | None
    candidate_package_reference: str
    previous_revision: str | None
    candidate_revision: str | None
    previous_artifact_sha256: str | None
    candidate_artifact_sha256: str
    previous_signature: str | None
    candidate_signature: str | None
    previous_signature_key_id: str | None
    candidate_signature_key_id: str | None
    previous_trust_status: TrustStatus | None
    candidate_trust_status: TrustStatus
    previous_review_reference: str | None
    candidate_review_reference: str | None

    @property
    def source_changed(self) -> bool:
        return self._known_changed(
            self.previous_source_registry,
            self.candidate_source_registry,
        )

    @property
    def publisher_changed(self) -> bool:
        return self._known_changed(self.previous_publisher, self.candidate_publisher)

    @property
    def repository_changed(self) -> bool:
        return self._known_changed(self.previous_repository, self.candidate_repository)

    @property
    def package_reference_changed(self) -> bool:
        return self._known_changed(
            self.previous_package_reference,
            self.candidate_package_reference,
        )

    @property
    def revision_changed(self) -> bool:
        return self.installed and self.previous_revision != self.candidate_revision

    @property
    def artifact_changed(self) -> bool:
        return self._known_changed(
            self.previous_artifact_sha256,
            self.candidate_artifact_sha256,
        )

    @property
    def signature_changed(self) -> bool:
        return self.installed and self.previous_signature != self.candidate_signature

    @property
    def signature_key_changed(self) -> bool:
        return self.installed and self.previous_signature_key_id != self.candidate_signature_key_id

    @property
    def trust_changed(self) -> bool:
        return (
            self.previous_trust_status is not None
            and self.previous_trust_status is not self.candidate_trust_status
        )

    @property
    def trust_downgraded(self) -> bool:
        if self.previous_trust_status is None:
            return False
        rank = {
            TrustStatus.UNTRUSTED: 0,
            TrustStatus.REVIEWED: 1,
            TrustStatus.TRUSTED: 2,
            TrustStatus.LOCAL: 2,
        }
        return rank[self.candidate_trust_status] < rank[self.previous_trust_status]

    @property
    def review_changed(self) -> bool:
        return (
            self.previous_review_reference is not None
            and self.previous_review_reference != self.candidate_review_reference
        )

    @property
    def changed(self) -> bool:
        return any(
            (
                self.source_changed,
                self.publisher_changed,
                self.repository_changed,
                self.package_reference_changed,
                self.revision_changed,
                self.artifact_changed,
                self.signature_changed,
                self.signature_key_changed,
                self.trust_changed,
                self.review_changed,
            )
        )

    def _known_changed(self, previous: str | None, candidate: str | None) -> bool:
        return self.installed and previous is not None and previous != candidate


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    required: bool
    reasons: tuple[str, ...] = ()
    authorization_required: bool = True


@dataclass(frozen=True, slots=True)
class UpdateState:
    installed_version: str | None
    candidate_version: str
    latest_compatible_version: str | None
    update_available: bool
    pinned: bool
    blocked_by_pin: bool
    incompatible_update: bool
    current_yanked: bool
    candidate_yanked: bool
    candidate_deprecated: bool
    source_change: bool
    permission_change: bool
    trust_integrity_issue: bool


@dataclass(frozen=True, slots=True)
class MarketplaceDecision:
    operation: DistributionOperation
    dependencies: tuple[DependencyResolution, ...]
    install_order: tuple[InstallPlanStep, ...]
    compatibility: CompatibilityDecision
    permission_diff: PermissionDiff
    provenance_diff: ProvenanceDiff
    approval: ApprovalRequirement
    update_state: UpdateState

    @property
    def dependency_blocked(self) -> bool:
        return any(resolution.blocking for resolution in self.dependencies)
