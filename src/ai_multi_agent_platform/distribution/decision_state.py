"""Permission, provenance, approval and update-state projections."""

from __future__ import annotations

from .decision_types import (
    ApprovalRequirement,
    DependencyChange,
    DependencyDiff,
    DependencyResolution,
    PermissionDiff,
    ProvenanceDiff,
    UpdateState,
)
from .dependency_graph import evaluate_compatibility, resolve_dependency_graph
from .items import RegistryItem
from .models import RegistryDependency, version_key
from .state import RegistryInstallation
from .validation import (
    FindingCategory,
    FindingSeverity,
    ValidationContext,
    ValidationFinding,
)


def build_dependency_diff(
    item: RegistryItem,
    installation: RegistryInstallation | None,
) -> DependencyDiff:
    requested = _sorted_dependencies(item.dependencies)
    if installation is None:
        return DependencyDiff(
            installed=False,
            previous_known=True,
            previous=(),
            requested=requested,
            added=requested,
            removed=(),
            changes=(),
            unchanged=(),
        )

    previous_raw = installation.current.dependencies
    if previous_raw is None:
        return DependencyDiff(
            installed=True,
            previous_known=False,
            previous=(),
            requested=requested,
            added=(),
            removed=(),
            changes=(),
            unchanged=(),
        )

    previous = _sorted_dependencies(previous_raw)
    unchanged, previous_only, requested_only = _partition_exact_dependency_matches(
        previous,
        requested,
    )
    changes, removed, added = _pair_dependency_changes(previous_only, requested_only)
    return DependencyDiff(
        installed=True,
        previous_known=True,
        previous=previous,
        requested=requested,
        added=added,
        removed=removed,
        changes=changes,
        unchanged=unchanged,
    )


def _partition_exact_dependency_matches(
    previous: tuple[RegistryDependency, ...],
    requested: tuple[RegistryDependency, ...],
) -> tuple[
    tuple[RegistryDependency, ...],
    tuple[RegistryDependency, ...],
    tuple[RegistryDependency, ...],
]:
    remaining_previous = list(previous)
    unchanged: list[RegistryDependency] = []
    requested_only: list[RegistryDependency] = []
    for dependency in requested:
        try:
            index = remaining_previous.index(dependency)
        except ValueError:
            requested_only.append(dependency)
        else:
            unchanged.append(dependency)
            remaining_previous.pop(index)
    return tuple(unchanged), tuple(remaining_previous), tuple(requested_only)


def _pair_dependency_changes(
    previous: tuple[RegistryDependency, ...],
    requested: tuple[RegistryDependency, ...],
) -> tuple[
    tuple[DependencyChange, ...],
    tuple[RegistryDependency, ...],
    tuple[RegistryDependency, ...],
]:
    previous_by_id = _dependencies_by_item_id(previous)
    requested_by_id = _dependencies_by_item_id(requested)
    changes: list[DependencyChange] = []
    removed: list[RegistryDependency] = []
    added: list[RegistryDependency] = []
    for item_id in sorted(previous_by_id.keys() | requested_by_id.keys()):
        old = previous_by_id.get(item_id, [])
        new = requested_by_id.get(item_id, [])
        paired = min(len(old), len(new))
        changes.extend(DependencyChange(old[index], new[index]) for index in range(paired))
        removed.extend(old[paired:])
        added.extend(new[paired:])
    return tuple(changes), _sorted_dependencies(removed), _sorted_dependencies(added)


def _dependencies_by_item_id(
    dependencies: tuple[RegistryDependency, ...],
) -> dict[str, list[RegistryDependency]]:
    grouped: dict[str, list[RegistryDependency]] = {}
    for dependency in dependencies:
        grouped.setdefault(dependency.item_id, []).append(dependency)
    return grouped


def _sorted_dependencies(
    dependencies: tuple[RegistryDependency, ...] | list[RegistryDependency],
) -> tuple[RegistryDependency, ...]:
    return tuple(sorted(dependencies, key=_dependency_sort_key))


def _dependency_sort_key(
    dependency: RegistryDependency,
) -> tuple[str, str, str, str, bool]:
    return (
        dependency.item_id,
        dependency.kind_value or "",
        dependency.version_range.minimum or "",
        dependency.version_range.maximum or "",
        dependency.optional,
    )


def build_permission_diff(
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


def build_provenance_diff(
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
        previous_license=current.license if current is not None else None,
        candidate_license=item.license,
        previous_provenance=current.provenance if current is not None else None,
        candidate_provenance=item.provenance,
        previous_repository=current.source_repository if current is not None else None,
        candidate_repository=item.source.repository,
        previous_package_reference=(current.package_reference if current is not None else None),
        candidate_package_reference=item.source.package_reference,
        previous_revision=current.revision if current is not None else None,
        candidate_revision=item.source.revision,
        previous_artifact_sha256=(current.artifact_sha256 if current is not None else None),
        candidate_artifact_sha256=artifact_sha256,
        previous_signature=current.signature if current is not None else None,
        candidate_signature=item.integrity.signature,
        previous_signature_key_id=(current.signature_key_id if current is not None else None),
        candidate_signature_key_id=item.integrity.signature_key_id,
        previous_trust_status=current.trust_status if current is not None else None,
        candidate_trust_status=item.trust_status,
        previous_review_reference=(current.review_reference if current is not None else None),
        candidate_review_reference=item.review_reference,
    )


def build_approval_requirement(
    permission_diff: PermissionDiff,
    provenance_diff: ProvenanceDiff,
) -> ApprovalRequirement:
    reasons: list[str] = []
    _append_reason(reasons, permission_diff.escalated, "new_permissions")
    _append_reason(reasons, provenance_diff.source_changed, "source_change")
    _append_reason(reasons, provenance_diff.publisher_changed, "publisher_change")
    _append_reason(reasons, provenance_diff.repository_changed, "repository_change")
    _append_reason(
        reasons,
        provenance_diff.signature_key_changed,
        "signature_key_change",
    )
    _append_reason(reasons, provenance_diff.trust_downgraded, "trust_downgrade")
    return ApprovalRequirement(bool(reasons), tuple(reasons))


def _append_reason(reasons: list[str], condition: bool, reason: str) -> None:
    if condition:
        reasons.append(reason)


def build_change_findings(
    dependency_diff: DependencyDiff,
    permission_diff: PermissionDiff,
    provenance_diff: ProvenanceDiff,
    approval: ApprovalRequirement,
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    if dependency_diff.installed and dependency_diff.previous_known and dependency_diff.changed:
        findings.append(_dependency_changed_finding(dependency_diff))
    if permission_diff.added:
        findings.append(_permission_added_finding(permission_diff))
    findings.extend(_provenance_change_findings(provenance_diff))
    if approval.required:
        findings.append(_approval_finding(approval))
    return tuple(findings)


def _dependency_changed_finding(dependency_diff: DependencyDiff) -> ValidationFinding:
    details = [
        *(("added", dependency.item_id) for dependency in dependency_diff.added),
        *(("removed", dependency.item_id) for dependency in dependency_diff.removed),
        *(("changed", change.requested.item_id) for change in dependency_diff.changes),
    ]
    return ValidationFinding(
        "dependency_changed",
        FindingSeverity.WARNING,
        "update changes declared Marketplace dependencies",
        FindingCategory.DEPENDENCY,
        "dependencies",
        tuple(details),
    )


def _permission_added_finding(permission_diff: PermissionDiff) -> ValidationFinding:
    return ValidationFinding(
        "permission_added",
        FindingSeverity.WARNING,
        "update requests new permissions: " + ", ".join(permission_diff.added),
        FindingCategory.PERMISSION,
        "permissions",
        tuple(("permission", permission) for permission in permission_diff.added),
    )


def _provenance_change_findings(
    provenance_diff: ProvenanceDiff,
) -> tuple[ValidationFinding, ...]:
    changes = (
        ("source_changed", provenance_diff.source_changed, "Marketplace source changed"),
        ("publisher_changed", provenance_diff.publisher_changed, "publisher changed"),
        (
            "repository_changed",
            provenance_diff.repository_changed,
            "source repository changed",
        ),
        (
            "signature_key_changed",
            provenance_diff.signature_key_changed,
            "artifact signature key changed",
        ),
        (
            "signature_changed",
            provenance_diff.signature_changed,
            "artifact signature changed",
        ),
        ("trust_downgrade", provenance_diff.trust_downgraded, "trust status was downgraded"),
    )
    return tuple(
        ValidationFinding(
            code,
            FindingSeverity.WARNING,
            message,
            FindingCategory.PROVENANCE,
            "provenance",
        )
        for code, changed, message in changes
        if changed
    )


def _approval_finding(approval: ApprovalRequirement) -> ValidationFinding:
    return ValidationFinding(
        "approval_required",
        FindingSeverity.WARNING,
        "existing approval/policy infrastructure must review security-sensitive changes",
        FindingCategory.POLICY,
        "approval",
        tuple(("reason", reason) for reason in approval.reasons),
    )


def build_update_state(
    item: RegistryItem,
    *,
    catalog: tuple[RegistryItem, ...],
    context: ValidationContext,
    installation: RegistryInstallation | None,
    dependencies: tuple[DependencyResolution, ...],
    permission_diff: PermissionDiff,
    provenance_diff: ProvenanceDiff,
    findings: tuple[ValidationFinding, ...],
) -> UpdateState:
    installed_version = installation.current.version if installation is not None else None
    same_item = tuple(candidate for candidate in catalog if candidate.item_id == item.item_id)
    candidates = _compatibility_candidates(item, same_item, installation)
    latest_compatible = _latest_compatible_version(
        candidates,
        context=context,
        catalog=catalog,
    )
    return UpdateState(
        installed_version=installed_version,
        candidate_version=item.version,
        latest_compatible_version=latest_compatible,
        update_available=_has_newer_candidate(installed_version, same_item),
        pinned=installation is not None and installation.pinned_version is not None,
        blocked_by_pin=_blocked_by_pin(item, installation),
        incompatible_update=_incompatible_update(
            item,
            installation=installation,
            dependencies=dependencies,
            context=context,
        ),
        current_yanked=_current_yanked(installation, same_item),
        candidate_yanked=item.yanked,
        candidate_deprecated=item.deprecated,
        source_change=provenance_diff.source_changed,
        permission_change=permission_diff.changed,
        trust_integrity_issue=_trust_integrity_issue(provenance_diff, findings),
    )


def _compatibility_candidates(
    item: RegistryItem,
    same_item: tuple[RegistryItem, ...],
    installation: RegistryInstallation | None,
) -> tuple[RegistryItem, ...]:
    if installation is None:
        return same_item
    source_registry = item.source_registry or installation.current.source_registry
    same_source = tuple(
        candidate for candidate in same_item if candidate.source_registry == source_registry
    )
    return same_source or same_item


def _latest_compatible_version(
    candidates: tuple[RegistryItem, ...],
    *,
    context: ValidationContext,
    catalog: tuple[RegistryItem, ...],
) -> str | None:
    ordered = sorted(
        candidates,
        key=lambda candidate: version_key(candidate.version),
        reverse=True,
    )
    for candidate in ordered:
        if _candidate_is_compatible(candidate, context=context, catalog=catalog):
            return candidate.version
    return None


def _candidate_is_compatible(
    candidate: RegistryItem,
    *,
    context: ValidationContext,
    catalog: tuple[RegistryItem, ...],
) -> bool:
    if candidate.yanked or not evaluate_compatibility(candidate, context).compatible:
        return False
    dependencies = resolve_dependency_graph(
        candidate,
        catalog=catalog,
        installed_items=context.installed_items,
        context=context,
    )
    return not any(resolution.blocking for resolution in dependencies)


def _has_newer_candidate(
    installed_version: str | None,
    candidates: tuple[RegistryItem, ...],
) -> bool:
    if installed_version is None:
        return False
    return any(
        version_key(candidate.version) > version_key(installed_version) for candidate in candidates
    )


def _blocked_by_pin(
    item: RegistryItem,
    installation: RegistryInstallation | None,
) -> bool:
    return (
        installation is not None
        and installation.pinned_version is not None
        and installation.pinned_version != item.version
    )


def _incompatible_update(
    item: RegistryItem,
    *,
    installation: RegistryInstallation | None,
    dependencies: tuple[DependencyResolution, ...],
    context: ValidationContext,
) -> bool:
    if installation is None:
        return False
    if item.yanked or not evaluate_compatibility(item, context).compatible:
        return True
    return any(resolution.blocking for resolution in dependencies)


def _current_yanked(
    installation: RegistryInstallation | None,
    candidates: tuple[RegistryItem, ...],
) -> bool:
    if installation is None:
        return False
    current = installation.current
    match = next(
        (
            candidate
            for candidate in candidates
            if candidate.version == current.version
            and candidate.source_registry == current.source_registry
        ),
        None,
    )
    return match.yanked if match is not None else current.yanked


def _trust_integrity_issue(
    provenance_diff: ProvenanceDiff,
    findings: tuple[ValidationFinding, ...],
) -> bool:
    if provenance_diff.trust_downgraded:
        return True
    return any(_finding_is_trust_integrity_issue(finding) for finding in findings)


def _finding_is_trust_integrity_issue(finding: ValidationFinding) -> bool:
    if finding.category not in {FindingCategory.INTEGRITY, FindingCategory.TRUST}:
        return False
    return finding.severity is FindingSeverity.ERROR or finding.code in {
        "untrusted",
        "trust_downgrade",
    }


def merge_findings(
    *groups: tuple[ValidationFinding, ...],
) -> tuple[ValidationFinding, ...]:
    merged: list[ValidationFinding] = []
    seen: set[tuple[str, str | None, str]] = set()
    for group in groups:
        _extend_unique_findings(merged, seen, group)
    return tuple(merged)


def _extend_unique_findings(
    merged: list[ValidationFinding],
    seen: set[tuple[str, str | None, str]],
    group: tuple[ValidationFinding, ...],
) -> None:
    for finding in group:
        key = (finding.code, finding.subject, finding.message)
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)
