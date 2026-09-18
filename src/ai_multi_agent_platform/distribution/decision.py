"""Marketplace pre-mutation decision facade."""

from __future__ import annotations

from .decision_state import (
    build_approval_requirement,
    build_change_findings,
    build_dependency_diff,
    build_permission_diff,
    build_provenance_diff,
    build_update_state,
    merge_findings,
)
from .decision_types import (
    ApprovalRequirement,
    CompatibilityDecision,
    DependencyDiff,
    DependencyResolution,
    DistributionOperation,
    MarketplaceDecision,
    PermissionDiff,
    ProvenanceDiff,
    UpdateState,
)
from .dependency_graph import (
    dependency_findings,
    deterministic_install_order,
    evaluate_compatibility,
    resolve_dependency_graph,
)
from .items import RegistryItem
from .models import TrustStatus
from .state import RegistryInstallation
from .validation import FindingCategory, FindingSeverity, ValidationContext, ValidationFinding


def build_marketplace_decision(
    item: RegistryItem,
    *,
    artifact_sha256: str,
    context: ValidationContext,
    catalog: tuple[RegistryItem, ...],
    installation: RegistryInstallation | None,
    validation_findings: tuple[ValidationFinding, ...],
) -> tuple[MarketplaceDecision, tuple[ValidationFinding, ...]]:
    dependencies = resolve_dependency_graph(
        item,
        catalog=catalog,
        installed_items=context.installed_items,
        context=context,
    )
    install_order = deterministic_install_order(item, dependencies)
    compatibility = evaluate_compatibility(item, context)
    dependency_diff = build_dependency_diff(item, installation)
    permission_diff = build_permission_diff(item, installation)
    provenance_diff = build_provenance_diff(
        item,
        artifact_sha256=artifact_sha256,
        installation=installation,
    )
    approval = build_approval_requirement(permission_diff, provenance_diff)
    findings = merge_findings(
        _without_direct_dependency_findings(validation_findings),
        dependency_findings(dependencies),
        build_change_findings(dependency_diff, permission_diff, provenance_diff, approval),
        _same_release_identity_findings(
            item,
            artifact_sha256=artifact_sha256,
            installation=installation,
        ),
    )
    update_state = build_update_state(
        item,
        catalog=catalog,
        context=context,
        installation=installation,
        dependencies=dependencies,
        permission_diff=permission_diff,
        provenance_diff=provenance_diff,
        findings=findings,
    )
    operation = (
        DistributionOperation.INSTALL if installation is None else DistributionOperation.UPDATE
    )
    return (
        MarketplaceDecision(
            operation=operation,
            dependencies=dependencies,
            install_order=install_order,
            compatibility=compatibility,
            dependency_diff=dependency_diff,
            permission_diff=permission_diff,
            provenance_diff=provenance_diff,
            approval=approval,
            update_state=update_state,
        ),
        findings,
    )


def _same_release_identity_findings(
    item: RegistryItem,
    *,
    artifact_sha256: str,
    installation: RegistryInstallation | None,
) -> tuple[ValidationFinding, ...]:
    if not _is_same_release_identity(item, installation):
        return ()

    assert installation is not None
    changed_fields = _same_release_identity_changed_fields(
        item,
        artifact_sha256=artifact_sha256,
        installation=installation,
    )
    if not changed_fields:
        return ()

    return (
        ValidationFinding(
            "immutable_release_drift",
            FindingSeverity.ERROR,
            (
                "installed Marketplace release evidence changed without a version "
                "or source identity change"
            ),
            FindingCategory.INTEGRITY,
            item.item_id,
            tuple(("changed_field", field) for field in changed_fields),
        ),
    )


def _is_same_release_identity(
    item: RegistryItem,
    installation: RegistryInstallation | None,
) -> bool:
    if installation is None:
        return False
    current = installation.current
    return current.version == item.version and current.source_registry == item.source_registry


def _same_release_identity_changed_fields(
    item: RegistryItem,
    *,
    artifact_sha256: str,
    installation: RegistryInstallation,
) -> tuple[str, ...]:
    current = installation.current
    return (
        *_changed_field_names(
            (
                ("source_repository", current.source_repository != item.source.repository),
                ("package_reference", current.package_reference != item.source.package_reference),
                ("revision", current.revision != item.source.revision),
                ("license", current.license != item.license),
                ("provenance", current.provenance != item.provenance),
                (
                    "item_type",
                    current.item_type is not None and current.as_installed().kind != item.kind,
                ),
                (
                    "artifact_sha256",
                    current.artifact_sha256 is not None
                    and current.artifact_sha256 != artifact_sha256,
                ),
            )
        ),
        *_current_schema_release_changed_fields(item, installation),
    )


def _current_schema_release_changed_fields(
    item: RegistryItem,
    installation: RegistryInstallation,
) -> tuple[str, ...]:
    current = installation.current
    if current.publisher is None:
        return ()
    return _changed_field_names(
        (
            ("publisher", current.publisher != item.publisher),
            (
                "dependencies",
                current.dependencies is not None and current.dependencies != item.dependencies,
            ),
            (
                "requested_permissions",
                current.requested_permissions != tuple(sorted(item.requested_permissions)),
            ),
            ("signature", current.signature != item.integrity.signature),
            ("signature_key_id", current.signature_key_id != item.integrity.signature_key_id),
            ("trust_status", current.trust_status != item.trust_status),
            ("review_reference", current.review_reference != item.review_reference),
        )
    )


def _changed_field_names(
    comparisons: tuple[tuple[str, bool], ...],
) -> tuple[str, ...]:
    return tuple(field for field, changed in comparisons if changed)


def _without_direct_dependency_findings(
    findings: tuple[ValidationFinding, ...],
) -> tuple[ValidationFinding, ...]:
    return tuple(
        finding for finding in findings if finding.category is not FindingCategory.DEPENDENCY
    )


def uninstall_decision(
    installation: RegistryInstallation,
    *,
    dependencies: tuple[DependencyResolution, ...] = (),
) -> MarketplaceDecision:
    current = installation.current
    permission_diff = PermissionDiff(
        previous=tuple(sorted(current.requested_permissions)),
        requested=(),
        added=(),
        removed=tuple(sorted(current.requested_permissions)),
        unchanged=(),
    )
    dependency_diff = _uninstall_dependency_diff(installation)
    provenance_diff = _uninstall_provenance_diff(installation)
    return MarketplaceDecision(
        operation=DistributionOperation.UNINSTALL,
        dependencies=dependencies,
        install_order=(),
        compatibility=CompatibilityDecision(True, True, True),
        dependency_diff=dependency_diff,
        permission_diff=permission_diff,
        provenance_diff=provenance_diff,
        approval=ApprovalRequirement(False),
        update_state=_uninstall_update_state(
            installation,
            permission_diff=permission_diff,
        ),
    )


def _uninstall_dependency_diff(
    installation: RegistryInstallation,
) -> DependencyDiff:
    previous_raw = installation.current.dependencies
    if previous_raw is None:
        return DependencyDiff(
            installed=True,
            previous_known=False,
            previous=(),
            requested=(),
            added=(),
            removed=(),
            changes=(),
            unchanged=(),
        )
    previous = tuple(previous_raw)
    return DependencyDiff(
        installed=True,
        previous_known=True,
        previous=previous,
        requested=(),
        added=(),
        removed=previous,
        changes=(),
        unchanged=(),
    )


def _uninstall_provenance_diff(
    installation: RegistryInstallation,
) -> ProvenanceDiff:
    current = installation.current
    return ProvenanceDiff(
        installed=True,
        previous_source_registry=current.source_registry,
        candidate_source_registry=current.source_registry,
        previous_publisher=current.publisher,
        candidate_publisher=current.publisher or "unknown",
        previous_license=current.license,
        candidate_license=current.license,
        previous_provenance=current.provenance,
        candidate_provenance=current.provenance,
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


def _uninstall_update_state(
    installation: RegistryInstallation,
    *,
    permission_diff: PermissionDiff,
) -> UpdateState:
    current = installation.current
    return UpdateState(
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
    )
