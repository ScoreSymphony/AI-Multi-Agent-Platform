"""Marketplace pre-mutation decision facade."""

from __future__ import annotations

from .decision_state import (
    build_approval_requirement,
    build_change_findings,
    build_permission_diff,
    build_provenance_diff,
    build_update_state,
    merge_findings,
)
from .decision_types import (
    ApprovalRequirement,
    CompatibilityDecision,
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
    )
    install_order = deterministic_install_order(item, dependencies)
    compatibility = evaluate_compatibility(item, context)
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
        build_change_findings(permission_diff, provenance_diff, approval),
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
    if installation is None:
        return ()

    current = installation.current
    if current.version != item.version or current.source_registry != item.source_registry:
        return ()

    changed_fields: list[str] = []
    if current.source_repository != item.source.repository:
        changed_fields.append("source_repository")
    if current.package_reference != item.source.package_reference:
        changed_fields.append("package_reference")
    if current.revision != item.source.revision:
        changed_fields.append("revision")
    if current.license != item.license:
        changed_fields.append("license")
    if current.provenance != item.provenance:
        changed_fields.append("provenance")
    if current.item_type is not None and current.as_installed().kind != item.kind:
        changed_fields.append("item_type")
    if current.artifact_sha256 is not None and current.artifact_sha256 != artifact_sha256:
        changed_fields.append("artifact_sha256")

    # publisher is present on the current durable state schema and acts as the
    # backward-compatibility boundary for evidence older snapshots did not retain.
    if current.publisher is not None:
        if current.publisher != item.publisher:
            changed_fields.append("publisher")
        if current.dependencies is not None and current.dependencies != item.dependencies:
            changed_fields.append("dependencies")
        if current.requested_permissions != tuple(sorted(item.requested_permissions)):
            changed_fields.append("requested_permissions")
        if current.signature != item.integrity.signature:
            changed_fields.append("signature")
        if current.signature_key_id != item.integrity.signature_key_id:
            changed_fields.append("signature_key_id")
        if current.trust_status != item.trust_status:
            changed_fields.append("trust_status")
        if current.review_reference != item.review_reference:
            changed_fields.append("review_reference")

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
    provenance_diff = _uninstall_provenance_diff(installation)
    return MarketplaceDecision(
        operation=DistributionOperation.UNINSTALL,
        dependencies=dependencies,
        install_order=(),
        compatibility=CompatibilityDecision(True, True, True),
        permission_diff=permission_diff,
        provenance_diff=provenance_diff,
        approval=ApprovalRequirement(False),
        update_state=_uninstall_update_state(
            installation,
            permission_diff=permission_diff,
        ),
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
