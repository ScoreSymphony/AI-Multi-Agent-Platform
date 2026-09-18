"""Typed Marketplace install/update decision planning across component kinds."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .items import InstalledRegistryItem, RegistryItem
from .models import RegistryDependency, TrustStatus, version_key
from .state import RegistryInstallation, RegistryInstallationSnapshot
from .validation import (
    FindingCategory,
    FindingSeverity,
    ValidationContext,
    ValidationFinding,
)


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
        if self.optional:
            return False
        return self.status is not DependencyStatus.SATISFIED


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
        return self._known_changed(self.previous_source_registry, self.candidate_source_registry)

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
        if self.previous_review_reference is None:
            return False
        return self.previous_review_reference != self.candidate_review_reference

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
    compatibility: CompatibilityDecision
    permission_diff: PermissionDiff
    provenance_diff: ProvenanceDiff
    approval: ApprovalRequirement
    update_state: UpdateState

    @property
    def dependency_blocked(self) -> bool:
        return any(resolution.blocking for resolution in self.dependencies)


def evaluate_compatibility(
    item: RegistryItem,
    context: ValidationContext,
) -> CompatibilityDecision:
    compatibility = item.compatibility
    os_compatible = _environment_value_supported(
        compatibility.operating_systems,
        context.operating_system,
    )
    architecture_compatible = _environment_value_supported(
        compatibility.architectures,
        context.architecture,
    )
    return CompatibilityDecision(
        platform_compatible=item.supported_platform.contains(context.platform_version),
        operating_system_compatible=os_compatible,
        architecture_compatible=architecture_compatible,
        missing_runtimes=tuple(
            sorted(compatibility.required_runtimes - context.available_runtimes)
        ),
        missing_capabilities=tuple(
            sorted(item.required_capabilities - context.available_capabilities)
        ),
        missing_plugins=tuple(
            sorted(set(item.required_plugins) - context.installed_plugins)
        ),
        missing_connectors=tuple(
            sorted(set(item.required_connectors) - context.installed_connectors)
        ),
        missing_models=tuple(sorted(set(item.required_models) - context.available_models)),
    )


def resolve_dependency_graph(
    item: RegistryItem,
    *,
    catalog: tuple[RegistryItem, ...],
    installed_items: tuple[InstalledRegistryItem, ...],
) -> tuple[DependencyResolution, ...]:
    installed = {record.item_id: record for record in installed_items}
    resolutions: list[DependencyResolution] = []
    visited_edges: set[tuple[str, str, tuple[str, ...]]] = set()

    def walk(parent: RegistryItem, path: tuple[str, ...]) -> None:
        for dependency in parent.dependencies:
            edge = (parent.item_id, dependency.item_id, path)
            if edge in visited_edges:
                continue
            visited_edges.add(edge)
            if dependency.item_id == parent.item_id:
                resolutions.append(
                    _resolution(
                        parent,
                        dependency,
                        DependencyStatus.SELF_DEPENDENCY,
                        path=(*path, dependency.item_id),
                    )
                )
                continue
            if dependency.item_id in path:
                resolutions.append(
                    _resolution(
                        parent,
                        dependency,
                        DependencyStatus.CYCLE,
                        path=(*path, dependency.item_id),
                    )
                )
                continue

            record = installed.get(dependency.item_id)
            candidate, catalog_status = _select_dependency_candidate(
                dependency,
                catalog,
                preferred_source=record.source_registry if record is not None else None,
            )

            if record is not None:
                status = _installed_dependency_status(dependency, record)
                if status is DependencyStatus.SATISFIED and catalog_status is DependencyStatus.SOURCE_AMBIGUOUS:
                    status = DependencyStatus.SATISFIED
                resolutions.append(
                    _resolution(
                        parent,
                        dependency,
                        status,
                        record=record,
                        candidate=candidate,
                        path=(*path, dependency.item_id),
                    )
                )
                if (
                    not dependency.optional
                    and status is DependencyStatus.SATISFIED
                    and candidate is not None
                ):
                    walk(candidate, (*path, dependency.item_id))
                continue

            status = catalog_status
            if status is DependencyStatus.MISSING and dependency.optional:
                status = DependencyStatus.OPTIONAL_MISSING
            resolutions.append(
                _resolution(
                    parent,
                    dependency,
                    status,
                    candidate=candidate,
                    path=(*path, dependency.item_id),
                )
            )
            if (
                not dependency.optional
                and status is DependencyStatus.AVAILABLE
                and candidate is not None
            ):
                walk(candidate, (*path, dependency.item_id))

    walk(item, (item.item_id,))
    return tuple(resolutions)


def dependency_findings(
    resolutions: tuple[DependencyResolution, ...],
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    for resolution in resolutions:
        status = resolution.status
        if status is DependencyStatus.SATISFIED:
            continue
        if status is DependencyStatus.OPTIONAL_MISSING:
            findings.append(
                _finding(
                    "optional_dependency_missing",
                    FindingSeverity.WARNING,
                    f"optional dependency {resolution.item_id} is not available",
                    resolution,
                )
            )
        elif status is DependencyStatus.AVAILABLE:
            findings.append(
                _finding(
                    "dependency_not_installed",
                    FindingSeverity.ERROR,
                    f"dependency {resolution.item_id} is available but not installed",
                    resolution,
                )
            )
        elif status is DependencyStatus.MISSING:
            findings.append(
                _finding(
                    "missing_dependency",
                    FindingSeverity.ERROR,
                    f"dependency {resolution.item_id} is missing",
                    resolution,
                )
            )
        elif status is DependencyStatus.VERSION_CONFLICT:
            findings.append(
                _finding(
                    "dependency_version",
                    FindingSeverity.ERROR,
                    f"dependency {resolution.item_id} has no compatible installed version",
                    resolution,
                )
            )
        elif status is DependencyStatus.KIND_UNKNOWN:
            findings.append(
                _finding(
                    "dependency_kind_unknown",
                    FindingSeverity.ERROR,
                    f"dependency {resolution.item_id} has no recorded component kind",
                    resolution,
                )
            )
        elif status is DependencyStatus.KIND_CONFLICT:
            findings.append(
                _finding(
                    "dependency_kind",
                    FindingSeverity.ERROR,
                    f"dependency {resolution.item_id} has an incompatible component kind",
                    resolution,
                )
            )
        elif status is DependencyStatus.SOURCE_AMBIGUOUS:
            findings.append(
                _finding(
                    "dependency_source_ambiguous",
                    FindingSeverity.ERROR,
                    f"dependency {resolution.item_id} is ambiguous across Marketplace sources",
                    resolution,
                )
            )
        elif status is DependencyStatus.SELF_DEPENDENCY:
            findings.append(
                _finding(
                    "self_dependency",
                    FindingSeverity.ERROR,
                    f"component {resolution.required_by} depends on itself",
                    resolution,
                )
            )
        elif status is DependencyStatus.CYCLE:
            findings.append(
                _finding(
                    "dependency_cycle",
                    FindingSeverity.ERROR,
                    "dependency cycle detected: " + " -> ".join(resolution.path),
                    resolution,
                )
            )
    return tuple(findings)


def build_marketplace_decision(
    item: RegistryItem,
    *,
    artifact_sha256: str,
    context: ValidationContext,
    catalog: tuple[RegistryItem, ...],
    installation: RegistryInstallation | None,
    validation_findings: tuple[ValidationFinding, ...],
) -> tuple[MarketplaceDecision, tuple[ValidationFinding, ...]]:
    operation = (
        DistributionOperation.INSTALL
        if installation is None
        else DistributionOperation.UPDATE
    )
    dependencies = resolve_dependency_graph(
        item,
        catalog=catalog,
        installed_items=context.installed_items,
    )
    compatibility = evaluate_compatibility(item, context)
    permission_diff = _permission_diff(item, installation)
    provenance_diff = _provenance_diff(
        item,
        artifact_sha256=artifact_sha256,
        installation=installation,
    )
    approval = _approval_requirement(permission_diff, provenance_diff)
    dependency_validation = dependency_findings(dependencies)
    change_findings = _change_findings(permission_diff, provenance_diff, approval)
    findings = _merge_findings(
        tuple(
            finding
            for finding in validation_findings
            if finding.category is not FindingCategory.DEPENDENCY
        ),
        dependency_validation,
        change_findings,
    )
    update_state = _update_state(
        item,
        catalog=catalog,
        context=context,
        installation=installation,
        dependencies=dependencies,
        compatibility=compatibility,
        permission_diff=permission_diff,
        provenance_diff=provenance_diff,
        findings=findings,
    )
    return (
        MarketplaceDecision(
            operation=operation,
            dependencies=dependencies,
            compatibility=compatibility,
            permission_diff=permission_diff,
            provenance_diff=provenance_diff,
            approval=approval,
            update_state=update_state,
        ),
        findings,
    )


def uninstall_decision(
    installation: RegistryInstallation,
) -> MarketplaceDecision:
    current = installation.current
    permission_diff = PermissionDiff(
        previous=tuple(sorted(current.requested_permissions)),
        requested=(),
        added=(),
        removed=tuple(sorted(current.requested_permissions)),
        unchanged=(),
    )
    provenance_diff = ProvenanceDiff(
        installed=True,
        previous_source_registry=current.source_registry,
        candidate_source_registry=current.source_registry,
        previous_publisher=current.publisher,
        candidate_publisher=current.publisher or "unknown",
        previous_repository=current.source_repository,
        candidate_repository=current.source_repository,
        previous_package_reference=current.package_reference,
        candidate_package_reference=current.package_reference,
        previous_revision=current.revision,
        candidate_revision=current.revision,
        previous_artifact_sha256=current.artifact_sha256,
        candidate_artifact_sha256=current.artifact_sha256 or "unknown",
        previous_signature=current.signature,
        candidate_signature=current.signature,
        previous_signature_key_id=current.signature_key_id,
        candidate_signature_key_id=current.signature_key_id,
        previous_trust_status=current.trust_status,
        candidate_trust_status=current.trust_status or TrustStatus.UNTRUSTED,
        previous_review_reference=current.review_reference,
        candidate_review_reference=current.review_reference,
    )
    return MarketplaceDecision(
        operation=DistributionOperation.UNINSTALL,
        dependencies=(),
        compatibility=CompatibilityDecision(True, True, True),
        permission_diff=permission_diff,
        provenance_diff=provenance_diff,
        approval=ApprovalRequirement(False),
        update_state=UpdateState(
            installed_version=current.version,
            candidate_version=current.version,
            latest_compatible_version=current.version,
            update_available=False,
            pinned=installation.pinned_version is not None,
            blocked_by_pin=False,
            incompatible_update=False,
            current_yanked=current.yanked,
            candidate_yanked=current.yanked,
            candidate_deprecated=current.deprecated,
            source_change=False,
            permission_change=permission_diff.changed,
            trust_integrity_issue=False,
        ),
    )


def _permission_diff(
    item: RegistryItem,
    installation: RegistryInstallation | None,
) -> PermissionDiff:
    previous = (
        frozenset(installation.current.requested_permissions)
        if installation is not None
        else frozenset()
    )
    requested = item.requested_permissions
    return PermissionDiff(
        previous=tuple(sorted(previous)),
        requested=tuple(sorted(requested)),
        added=tuple(sorted(requested - previous)),
        removed=tuple(sorted(previous - requested)),
        unchanged=tuple(sorted(previous & requested)),
    )


def _provenance_diff(
    item: RegistryItem,
    *,
    artifact_sha256: str,
    installation: RegistryInstallation | None,
) -> ProvenanceDiff:
    current = installation.current if installation is not None else None
    return ProvenanceDiff(
        installed=current is not None,
        previous_source_registry=current.source_registry if current is not None else None,
        candidate_source_registry=item.source_registry,
        previous_publisher=current.publisher if current is not None else None,
        candidate_publisher=item.publisher,
        previous_repository=current.source_repository if current is not None else None,
        candidate_repository=item.source.repository,
        previous_package_reference=current.package_reference if current is not None else None,
        candidate_package_reference=item.source.package_reference,
        previous_revision=current.revision if current is not None else None,
        candidate_revision=item.source.revision,
        previous_artifact_sha256=current.artifact_sha256 if current is not None else None,
        candidate_artifact_sha256=artifact_sha256,
        previous_signature=current.signature if current is not None else None,
        candidate_signature=item.integrity.signature,
        previous_signature_key_id=current.signature_key_id if current is not None else None,
        candidate_signature_key_id=item.integrity.signature_key_id,
        previous_trust_status=current.trust_status if current is not None else None,
        candidate_trust_status=item.trust_status,
        previous_review_reference=current.review_reference if current is not None else None,
        candidate_review_reference=item.review_reference,
    )


def _approval_requirement(
    permission_diff: PermissionDiff,
    provenance_diff: ProvenanceDiff,
) -> ApprovalRequirement:
    reasons: list[str] = []
    if permission_diff.added:
        reasons.append("new_permissions")
    if provenance_diff.source_changed:
        reasons.append("source_change")
    if provenance_diff.publisher_changed:
        reasons.append("publisher_change")
    if provenance_diff.repository_changed:
        reasons.append("repository_change")
    if provenance_diff.signature_key_changed:
        reasons.append("signature_key_change")
    if provenance_diff.signature_changed:
        reasons.append("signature_change")
    if provenance_diff.trust_downgraded:
        reasons.append("trust_downgrade")
    return ApprovalRequirement(bool(reasons), tuple(reasons))


def _change_findings(
    permission_diff: PermissionDiff,
    provenance_diff: ProvenanceDiff,
    approval: ApprovalRequirement,
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    if permission_diff.added:
        findings.append(
            ValidationFinding(
                "permission_added",
                FindingSeverity.WARNING,
                "update requests new permissions: " + ", ".join(permission_diff.added),
                FindingCategory.PERMISSION,
                "permissions",
                tuple(("permission", permission) for permission in permission_diff.added),
            )
        )
    for code, changed, message in (
        ("source_changed", provenance_diff.source_changed, "Marketplace source changed"),
        ("publisher_changed", provenance_diff.publisher_changed, "publisher changed"),
        ("repository_changed", provenance_diff.repository_changed, "source repository changed"),
        (
            "signature_key_changed",
            provenance_diff.signature_key_changed,
            "artifact signature key changed",
        ),
        ("signature_changed", provenance_diff.signature_changed, "artifact signature changed"),
        ("trust_downgrade", provenance_diff.trust_downgraded, "trust status was downgraded"),
    ):
        if changed:
            findings.append(
                ValidationFinding(
                    code,
                    FindingSeverity.WARNING,
                    message,
                    FindingCategory.PROVENANCE,
                    "provenance",
                )
            )
    if approval.required:
        findings.append(
            ValidationFinding(
                "approval_required",
                FindingSeverity.WARNING,
                "existing approval/policy infrastructure must review security-sensitive changes",
                FindingCategory.POLICY,
                "approval",
                tuple(("reason", reason) for reason in approval.reasons),
            )
        )
    return tuple(findings)


def _update_state(
    item: RegistryItem,
    *,
    catalog: tuple[RegistryItem, ...],
    context: ValidationContext,
    installation: RegistryInstallation | None,
    dependencies: tuple[DependencyResolution, ...],
    compatibility: CompatibilityDecision,
    permission_diff: PermissionDiff,
    provenance_diff: ProvenanceDiff,
    findings: tuple[ValidationFinding, ...],
) -> UpdateState:
    installed_version = installation.current.version if installation is not None else None
    same_item = tuple(candidate for candidate in catalog if candidate.item_id == item.item_id)
    if installation is not None:
        same_source = tuple(
            candidate
            for candidate in same_item
            if candidate.source_registry == installation.current.source_registry
        )
        candidates_for_compatibility = same_source or same_item
    else:
        candidates_for_compatibility = same_item
    latest_compatible = _latest_compatible_version(
        candidates_for_compatibility,
        context=context,
        catalog=catalog,
    )
    update_available = False
    if installed_version is not None:
        update_available = any(
            version_key(candidate.version) > version_key(installed_version)
            for candidate in same_item
        )
    pinned = installation is not None and installation.pinned_version is not None
    blocked_by_pin = (
        installation is not None
        and installation.pinned_version is not None
        and installation.pinned_version != item.version
    )
    incompatible_update = installation is not None and (
        not compatibility.compatible
        or any(resolution.blocking for resolution in dependencies)
        or item.yanked
    )
    current_yanked = _current_yanked(installation, same_item)
    trust_integrity_issue = provenance_diff.trust_downgraded or any(
        finding.category in {FindingCategory.INTEGRITY, FindingCategory.TRUST}
        and (
            finding.severity is FindingSeverity.ERROR
            or finding.code in {"untrusted", "trust_downgrade"}
        )
        for finding in findings
    )
    return UpdateState(
        installed_version=installed_version,
        candidate_version=item.version,
        latest_compatible_version=latest_compatible,
        update_available=update_available,
        pinned=pinned,
        blocked_by_pin=blocked_by_pin,
        incompatible_update=incompatible_update,
        current_yanked=current_yanked,
        candidate_yanked=item.yanked,
        candidate_deprecated=item.deprecated,
        source_change=provenance_diff.source_changed,
        permission_change=permission_diff.changed,
        trust_integrity_issue=trust_integrity_issue,
    )


def _latest_compatible_version(
    candidates: tuple[RegistryItem, ...],
    *,
    context: ValidationContext,
    catalog: tuple[RegistryItem, ...],
) -> str | None:
    for candidate in sorted(candidates, key=lambda item: version_key(item.version), reverse=True):
        if candidate.yanked:
            continue
        compatibility = evaluate_compatibility(candidate, context)
        if not compatibility.compatible:
            continue
        dependencies = resolve_dependency_graph(
            candidate,
            catalog=catalog,
            installed_items=context.installed_items,
        )
        if any(resolution.blocking for resolution in dependencies):
            continue
        return candidate.version
    return None


def _current_yanked(
    installation: RegistryInstallation | None,
    candidates: tuple[RegistryItem, ...],
) -> bool:
    if installation is None:
        return False
    current = installation.current
    for candidate in candidates:
        if (
            candidate.version == current.version
            and candidate.source_registry == current.source_registry
        ):
            return candidate.yanked
    return current.yanked


def _installed_dependency_status(
    dependency: RegistryDependency,
    record: InstalledRegistryItem,
) -> DependencyStatus:
    if dependency.kind_value is not None:
        if record.kind is None:
            return DependencyStatus.KIND_UNKNOWN
        if record.kind != dependency.kind_value:
            return DependencyStatus.KIND_CONFLICT
    if not dependency.version_range.contains(record.version):
        return DependencyStatus.VERSION_CONFLICT
    return DependencyStatus.SATISFIED


def _select_dependency_candidate(
    dependency: RegistryDependency,
    catalog: tuple[RegistryItem, ...],
    *,
    preferred_source: str | None,
) -> tuple[RegistryItem | None, DependencyStatus]:
    by_id = tuple(candidate for candidate in catalog if candidate.item_id == dependency.item_id)
    if not by_id:
        return None, DependencyStatus.MISSING
    by_kind = (
        tuple(candidate for candidate in by_id if candidate.kind == dependency.kind_value)
        if dependency.kind_value is not None
        else by_id
    )
    if not by_kind:
        return None, DependencyStatus.KIND_CONFLICT
    by_version = tuple(
        candidate
        for candidate in by_kind
        if dependency.version_range.contains(candidate.version) and not candidate.yanked
    )
    if not by_version:
        return None, DependencyStatus.VERSION_CONFLICT

    if preferred_source is not None:
        preferred = tuple(
            candidate
            for candidate in by_version
            if candidate.source_registry == preferred_source
        )
        if preferred:
            return max(preferred, key=lambda item: version_key(item.version)), DependencyStatus.AVAILABLE

    latest_version = max(by_version, key=lambda item: version_key(item.version)).version
    latest = tuple(candidate for candidate in by_version if candidate.version == latest_version)
    sources = {candidate.source_registry for candidate in latest}
    if len(sources) > 1:
        return None, DependencyStatus.SOURCE_AMBIGUOUS
    return latest[0], DependencyStatus.AVAILABLE


def _resolution(
    parent: RegistryItem,
    dependency: RegistryDependency,
    status: DependencyStatus,
    *,
    record: InstalledRegistryItem | None = None,
    candidate: RegistryItem | None = None,
    path: tuple[str, ...],
) -> DependencyResolution:
    return DependencyResolution(
        required_by=parent.item_id,
        item_id=dependency.item_id,
        item_kind=dependency.kind_value,
        optional=dependency.optional,
        minimum_version=dependency.version_range.minimum,
        maximum_version=dependency.version_range.maximum,
        status=status,
        installed_version=record.version if record is not None else None,
        candidate_version=candidate.version if candidate is not None else None,
        candidate_kind=candidate.kind if candidate is not None else None,
        candidate_source_registry=(
            candidate.source_registry if candidate is not None else None
        ),
        path=path,
    )


def _finding(
    code: str,
    severity: FindingSeverity,
    message: str,
    resolution: DependencyResolution,
) -> ValidationFinding:
    details = [
        ("required_by", resolution.required_by),
        ("status", resolution.status.value),
    ]
    if resolution.item_kind is not None:
        details.append(("required_kind", resolution.item_kind))
    if resolution.installed_version is not None:
        details.append(("installed_version", resolution.installed_version))
    if resolution.candidate_version is not None:
        details.append(("candidate_version", resolution.candidate_version))
    if resolution.candidate_source_registry is not None:
        details.append(("candidate_source_registry", resolution.candidate_source_registry))
    return ValidationFinding(
        code,
        severity,
        message,
        FindingCategory.DEPENDENCY,
        resolution.item_id,
        tuple(details),
    )


def _merge_findings(
    *groups: tuple[ValidationFinding, ...],
) -> tuple[ValidationFinding, ...]:
    merged: list[ValidationFinding] = []
    seen: set[tuple[str, str | None, str]] = set()
    for group in groups:
        for finding in group:
            key = (finding.code, finding.subject, finding.message)
            if key in seen:
                continue
            seen.add(key)
            merged.append(finding)
    return tuple(merged)


def _environment_value_supported(
    supported: frozenset[str],
    current: str | None,
) -> bool:
    if not supported:
        return True
    if current is None:
        return False
    normalized = current.strip().casefold()
    return normalized in {value.strip().casefold() for value in supported}
