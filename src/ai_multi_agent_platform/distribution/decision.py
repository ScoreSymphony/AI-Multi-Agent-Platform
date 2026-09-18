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
    DependencyStatus,
    DistributionOperation,
    MarketplaceDecision,
    PermissionDiff,
    ProvenanceDiff,
    UpdateState,
)
from .dependency_graph import (
    dependency_findings,
    evaluate_compatibility,
    resolve_dependency_graph,
)
from .items import RegistryItem
from .models import TrustStatus
from .state import RegistryInstallation
from .validation import FindingCategory, ValidationContext, ValidationFinding


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
            compatibility=compatibility,
            permission_diff=permission_diff,
            provenance_diff=provenance_diff,
            approval=approval,
            update_state=update_state,
        ),
        findings,
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
