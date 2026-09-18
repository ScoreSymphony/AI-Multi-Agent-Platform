"""Serialization helpers for the unified Marketplace Control Plane surface."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .decision_types import (
    ApprovalRequirement,
    CompatibilityDecision,
    DependencyResolution,
    MarketplaceDecision,
    PermissionDiff,
    ProvenanceDiff,
    UpdateState,
)
from .items import RegistryItem
from .models import DistributionRoute
from .service import DistributionPreview
from .state import (
    RegistryInstallation,
    RegistryInstallationSnapshot,
)
from .validation import ValidationFinding


def _json_strings(values: Iterable[str]) -> list[JsonValue]:
    return [cast(JsonValue, value) for value in values]


def _is_update(
    item: RegistryItem,
    installation: RegistryInstallation | None,
) -> bool:
    return installation is not None and installation.as_installed().accepts_update(item)


def _dependency_metadata_resources(item: RegistryItem) -> list[JsonValue]:
    return [
        {
            "item_id": dependency.item_id,
            "item_kind": dependency.kind_value,
            "minimum_version": dependency.version_range.minimum,
            "maximum_version": dependency.version_range.maximum,
            "optional": dependency.optional,
        }
        for dependency in item.dependencies
    ]


def _source_resource(item: RegistryItem) -> dict[str, JsonValue]:
    return {
        "registry": item.source_registry,
        "repository": item.source.repository,
        "package_reference": item.source.package_reference,
        "revision": item.source.revision,
    }


def _manifest_resource(item: RegistryItem) -> dict[str, JsonValue] | None:
    if item.manifest is None:
        return None
    return {
        "kind": item.manifest.kind_value,
        "reference": item.manifest.reference,
        "schema_version": item.manifest.schema_version,
    }


def _integrity_resource(item: RegistryItem) -> dict[str, JsonValue]:
    return {
        "sha256": item.integrity.sha256,
        "signature_present": item.integrity.signature is not None,
        "signature_key_id": item.integrity.signature_key_id,
    }


def _compatibility_resource(
    item: RegistryItem,
    *,
    platform_compatible: bool | None,
    decision: CompatibilityDecision | None,
) -> dict[str, JsonValue]:
    return {
        "minimum_platform_version": item.supported_platform.minimum,
        "maximum_platform_version": item.supported_platform.maximum,
        "compatible": decision.compatible if decision is not None else platform_compatible,
        "platform_compatible": platform_compatible,
        "operating_system_compatible": (
            decision.operating_system_compatible if decision is not None else None
        ),
        "architecture_compatible": (
            decision.architecture_compatible if decision is not None else None
        ),
        "operating_systems": _json_strings(sorted(item.compatibility.operating_systems)),
        "architectures": _json_strings(sorted(item.compatibility.architectures)),
        "required_runtimes": _json_strings(sorted(item.compatibility.required_runtimes)),
        "missing_runtimes": _json_strings(decision.missing_runtimes) if decision else [],
        "missing_capabilities": (_json_strings(decision.missing_capabilities) if decision else []),
        "missing_plugins": _json_strings(decision.missing_plugins) if decision else [],
        "missing_connectors": (_json_strings(decision.missing_connectors) if decision else []),
        "missing_models": _json_strings(decision.missing_models) if decision else [],
    }


def _update_state_resource(
    item: RegistryItem,
    installation: RegistryInstallation | None,
    *,
    update_available: bool,
) -> dict[str, JsonValue]:
    return {
        "installed": installation is not None,
        "installed_version": installation.current.version if installation else None,
        "installed_source_registry": (
            installation.current.source_registry if installation else None
        ),
        "installation_source_matches": (
            installation.current.source_registry == item.source_registry if installation else None
        ),
        "candidate_version": item.version,
        "pinned_version": installation.pinned_version if installation else None,
        "update_available": update_available,
    }


def _item_resource(
    item: RegistryItem,
    installation: RegistryInstallation | None = None,
    *,
    update_available: bool = False,
    platform_compatible: bool | None = None,
    compatibility_decision: CompatibilityDecision | None = None,
    route: DistributionRoute | None = None,
    route_available: bool | None = None,
    owner_extension: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    effective_route = route or item.route
    qualified_id = (
        f"{item.source_registry}::{item.item_id}@{item.version}"
        if item.source_registry is not None
        else f"{item.item_id}@{item.version}"
    )
    return {
        "id": f"{item.item_id}@{item.version}",
        "qualified_id": qualified_id,
        "type": "registry-item",
        "item_id": item.item_id,
        "item_type": item.kind,
        "kind": item.kind,
        "name": item.name,
        "description": item.description,
        "version": item.version,
        "publisher": item.publisher,
        "source_registry": item.source_registry,
        "source": _source_resource(item),
        "license": item.license,
        "provenance": item.provenance,
        "minimum_platform_version": item.supported_platform.minimum,
        "maximum_platform_version": item.supported_platform.maximum,
        "compatibility": _compatibility_resource(
            item,
            platform_compatible=platform_compatible,
            decision=compatibility_decision,
        ),
        "dependencies": _dependency_metadata_resources(item),
        "requested_permissions": _json_strings(sorted(item.requested_permissions)),
        "required_capabilities": _json_strings(sorted(item.required_capabilities)),
        "required_plugins": _json_strings(item.required_plugins),
        "required_connectors": _json_strings(item.required_connectors),
        "required_models": _json_strings(item.required_models),
        "tags": _json_strings(sorted(item.tags)),
        "categories": _json_strings(sorted(item.categories)),
        "trust_status": item.trust_status.value,
        "trust": item.trust_status.value,
        "maturity": item.maturity.value if item.maturity is not None else None,
        "stability": item.maturity.value if item.maturity is not None else None,
        "review_reference": item.review_reference,
        "released_at": item.released_at,
        "release_date": item.released_at,
        "changelog": item.changelog,
        "deprecated": item.deprecated,
        "yanked": item.yanked,
        "route": effective_route.value,
        "route_available": route_available,
        "manifest_reference": _manifest_resource(item),
        "integrity": _integrity_resource(item),
        "installed": installation is not None,
        "installed_version": installation.current.version if installation else None,
        "installed_source_registry": (
            installation.current.source_registry if installation else None
        ),
        "installation_source_matches": (
            installation.current.source_registry == item.source_registry if installation else None
        ),
        "pinned_version": installation.pinned_version if installation else None,
        "update_available": update_available,
        "installation": _installation_resource(installation) if installation else None,
        "update_state": _update_state_resource(
            item,
            installation,
            update_available=update_available,
        ),
        "owner_extension": owner_extension,
    }


def _installation_history_resource(
    snapshot: RegistryInstallationSnapshot,
) -> dict[str, JsonValue]:
    return {
        "version": snapshot.version,
        "source_registry": snapshot.source_registry,
        "source_repository": snapshot.source_repository,
        "package_reference": snapshot.package_reference,
        "revision": snapshot.revision,
        "license": snapshot.license,
        "provenance": snapshot.provenance,
    }


def _installation_resource(
    installation: RegistryInstallation,
) -> dict[str, JsonValue]:
    current = installation.current
    history: list[JsonValue] = [
        _installation_history_resource(snapshot) for snapshot in installation.history
    ]
    return {
        "id": current.item_id,
        "type": "registry-installation",
        "item_id": current.item_id,
        "version": current.version,
        "pinned_version": installation.pinned_version,
        "source_registry": current.source_registry,
        "source_repository": current.source_repository,
        "package_reference": current.package_reference,
        "revision": current.revision,
        "license": current.license,
        "provenance": current.provenance,
        "publisher": current.publisher,
        "requested_permissions": _json_strings(current.requested_permissions),
        "trust_status": current.trust_status.value if current.trust_status else None,
        "review_reference": current.review_reference,
        "artifact_sha256": current.artifact_sha256,
        "deprecated": current.deprecated,
        "yanked": current.yanked,
        "history": history,
    }


def _finding_resource(finding: ValidationFinding) -> dict[str, JsonValue]:
    return {
        "code": finding.code,
        "severity": finding.severity.value,
        "category": finding.category.value,
        "subject": finding.subject,
        "message": finding.message,
        "details": {key: value for key, value in finding.details},
    }


def _dependency_decision_resource(
    dependency: DependencyResolution,
) -> dict[str, JsonValue]:
    return {
        "required_by": dependency.required_by,
        "item_id": dependency.item_id,
        "item_kind": dependency.item_kind,
        "optional": dependency.optional,
        "minimum_version": dependency.minimum_version,
        "maximum_version": dependency.maximum_version,
        "status": dependency.status.value,
        "installed_version": dependency.installed_version,
        "candidate_version": dependency.candidate_version,
        "candidate_kind": dependency.candidate_kind,
        "candidate_source_registry": dependency.candidate_source_registry,
        "path": _json_strings(dependency.path),
        "blocking": dependency.blocking,
    }


def _install_order_resource(decision: MarketplaceDecision) -> list[JsonValue]:
    return [
        {
            "item_id": step.item_id,
            "item_kind": step.item_kind,
            "version": step.version,
            "source_registry": step.source_registry,
        }
        for step in decision.install_order
    ]


def _compatibility_decision_resource(
    decision: CompatibilityDecision,
) -> dict[str, JsonValue]:
    return {
        "compatible": decision.compatible,
        "platform_compatible": decision.platform_compatible,
        "operating_system_compatible": decision.operating_system_compatible,
        "architecture_compatible": decision.architecture_compatible,
        "missing_runtimes": _json_strings(decision.missing_runtimes),
        "missing_capabilities": _json_strings(decision.missing_capabilities),
        "missing_plugins": _json_strings(decision.missing_plugins),
        "missing_connectors": _json_strings(decision.missing_connectors),
        "missing_models": _json_strings(decision.missing_models),
    }


def _permission_diff_resource(diff: PermissionDiff) -> dict[str, JsonValue]:
    return {
        "previous": _json_strings(diff.previous),
        "requested": _json_strings(diff.requested),
        "added": _json_strings(diff.added),
        "removed": _json_strings(diff.removed),
        "unchanged": _json_strings(diff.unchanged),
        "changed": diff.changed,
        "escalated": diff.escalated,
    }


def _provenance_diff_resource(diff: ProvenanceDiff) -> dict[str, JsonValue]:
    return {
        "installed": diff.installed,
        "previous_source_registry": diff.previous_source_registry,
        "candidate_source_registry": diff.candidate_source_registry,
        "previous_publisher": diff.previous_publisher,
        "candidate_publisher": diff.candidate_publisher,
        "previous_repository": diff.previous_repository,
        "candidate_repository": diff.candidate_repository,
        "previous_package_reference": diff.previous_package_reference,
        "candidate_package_reference": diff.candidate_package_reference,
        "previous_revision": diff.previous_revision,
        "candidate_revision": diff.candidate_revision,
        "previous_artifact_sha256": diff.previous_artifact_sha256,
        "candidate_artifact_sha256": diff.candidate_artifact_sha256,
        "previous_signature_key_id": diff.previous_signature_key_id,
        "candidate_signature_key_id": diff.candidate_signature_key_id,
        "previous_trust_status": (
            diff.previous_trust_status.value if diff.previous_trust_status else None
        ),
        "candidate_trust_status": diff.candidate_trust_status.value,
        "previous_review_reference": diff.previous_review_reference,
        "candidate_review_reference": diff.candidate_review_reference,
        "source_changed": diff.source_changed,
        "publisher_changed": diff.publisher_changed,
        "repository_changed": diff.repository_changed,
        "package_reference_changed": diff.package_reference_changed,
        "revision_changed": diff.revision_changed,
        "artifact_changed": diff.artifact_changed,
        "signature_changed": diff.signature_changed,
        "signature_key_changed": diff.signature_key_changed,
        "trust_changed": diff.trust_changed,
        "trust_downgraded": diff.trust_downgraded,
        "review_changed": diff.review_changed,
        "changed": diff.changed,
    }


def _approval_resource(approval: ApprovalRequirement) -> dict[str, JsonValue]:
    return {
        "required": approval.required,
        "reasons": _json_strings(approval.reasons),
        "authorization_required": approval.authorization_required,
    }


def _decision_update_resource(update: UpdateState) -> dict[str, JsonValue]:
    return {
        "installed_version": update.installed_version,
        "candidate_version": update.candidate_version,
        "latest_compatible_version": update.latest_compatible_version,
        "update_available": update.update_available,
        "pinned": update.pinned,
        "blocked_by_pin": update.blocked_by_pin,
        "incompatible_update": update.incompatible_update,
        "current_yanked": update.current_yanked,
        "candidate_yanked": update.candidate_yanked,
        "candidate_deprecated": update.candidate_deprecated,
        "source_change": update.source_change,
        "permission_change": update.permission_change,
        "trust_integrity_issue": update.trust_integrity_issue,
    }


def _decision_resource(decision: MarketplaceDecision) -> dict[str, JsonValue]:
    dependencies: list[JsonValue] = [
        _dependency_decision_resource(dependency) for dependency in decision.dependencies
    ]
    return {
        "operation": decision.operation.value,
        "dependencies": dependencies,
        "install_order": _install_order_resource(decision),
        "compatibility": _compatibility_decision_resource(decision.compatibility),
        "permission_diff": _permission_diff_resource(decision.permission_diff),
        "provenance_diff": _provenance_diff_resource(decision.provenance_diff),
        "approval": _approval_resource(decision.approval),
        "update_state": _decision_update_resource(decision.update_state),
        "dependency_blocked": decision.dependency_blocked,
    }


def _marketplace_mutation_resource(
    action: str,
    preview: DistributionPreview,
    installation: RegistryInstallation | None,
) -> dict[str, JsonValue]:
    return {
        "id": f"{preview.item.item_id}@{preview.item.version}",
        "type": "marketplace-mutation",
        "action": action,
        "status": "applied",
        "route": preview.route.value,
        "decision": _decision_resource(preview.decision),
        "installation": _installation_resource(installation) if installation else None,
    }


def _preview_resource(
    preview: DistributionPreview,
    installation: RegistryInstallation | None = None,
    *,
    route_available: bool | None = None,
    activation_allowed: bool | None = None,
    include_decision: bool = False,
) -> dict[str, JsonValue]:
    resource: dict[str, JsonValue] = {
        "id": f"{preview.item.item_id}@{preview.item.version}",
        "type": "registry-preview",
        "provider_id": preview.provider_id,
        "artifact_sha256": preview.artifact_sha256,
        "item": _item_resource(
            preview.item,
            installation,
            update_available=_is_update(preview.item, installation),
            route=preview.route,
            route_available=route_available,
        ),
        "route": preview.route.value,
        "activation_allowed": (
            preview.activation_allowed if activation_allowed is None else activation_allowed
        ),
        "findings": [_finding_resource(finding) for finding in preview.findings],
    }
    if include_decision:
        resource["decision"] = _decision_resource(preview.decision)
    return resource
