"""Compatibility, integrity and risk validation for registry artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from .items import InstalledRegistryItem, RegistryItem


class FindingSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    severity: FindingSeverity
    message: str


@dataclass(frozen=True, slots=True)
class ValidationContext:
    platform_version: str
    installed_items: tuple[InstalledRegistryItem, ...] = ()
    available_capabilities: frozenset[str] = frozenset()
    installed_plugins: frozenset[str] = frozenset()
    installed_connectors: frozenset[str] = frozenset()
    available_models: frozenset[str] = frozenset()
    grantable_permissions: frozenset[str] = frozenset()
    signature_valid: bool | None = None


def validate_item(
    item: RegistryItem,
    artifact: bytes,
    context: ValidationContext,
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    if not item.supported_platform.contains(context.platform_version):
        findings.append(_error("incompatible_platform", "platform version is not supported"))
    if item.yanked:
        findings.append(_error("yanked", "registry release is yanked"))
    if item.deprecated:
        findings.append(_warning("deprecated", "registry item is deprecated"))
    if item.integrity.sha256 is not None:
        actual = hashlib.sha256(artifact).hexdigest()
        if actual != item.integrity.sha256:
            findings.append(_error("checksum_mismatch", "artifact checksum validation failed"))
    if item.integrity.signature is not None:
        if context.signature_valid is False:
            findings.append(_error("signature_failure", "artifact signature validation failed"))
        elif context.signature_valid is None:
            findings.append(
                _error(
                    "signature_unverified",
                    "signature metadata is present but no trusted verifier could validate it",
                )
            )

    installed = {record.item_id: record for record in context.installed_items}
    for dependency in item.dependencies:
        record = installed.get(dependency.item_id)
        if record is None:
            if not dependency.optional:
                findings.append(_error("missing_dependency", f"missing {dependency.item_id}"))
        elif not dependency.version_range.contains(record.version):
            findings.append(
                _error("dependency_version", f"incompatible dependency {dependency.item_id}")
            )

    missing_permissions = item.requested_permissions - context.grantable_permissions
    if missing_permissions:
        findings.append(
            _error(
                "permission_escalation",
                "requested permissions are not grantable: "
                + ", ".join(sorted(missing_permissions)),
            )
        )
    _require_set(item.required_capabilities, context.available_capabilities, "capability", findings)
    _require_set(frozenset(item.required_plugins), context.installed_plugins, "plugin", findings)
    _require_set(
        frozenset(item.required_connectors), context.installed_connectors, "connector", findings
    )
    _require_set(frozenset(item.required_models), context.available_models, "model", findings)

    current = installed.get(item.item_id)
    if current is not None:
        if current.license is not None and current.license != item.license:
            findings.append(
                _warning("license_changed", "license metadata changed since installation")
            )
        if current.provenance is not None and current.provenance != item.provenance:
            findings.append(_warning("provenance_changed", "provenance metadata changed"))
        if current.pinned_version is not None and current.pinned_version != item.version:
            findings.append(_error("version_pinned", "installed item is pinned to another version"))
    if item.trust_status.value == "untrusted":
        findings.append(_warning("untrusted", "registry content is explicitly untrusted"))
    return tuple(findings)


def has_errors(findings: tuple[ValidationFinding, ...]) -> bool:
    return any(item.severity is FindingSeverity.ERROR for item in findings)


def _require_set(
    required: frozenset[str],
    available: frozenset[str],
    label: str,
    findings: list[ValidationFinding],
) -> None:
    missing = required - available
    if missing:
        findings.append(
            _error(f"missing_{label}", f"missing {label}: " + ", ".join(sorted(missing)))
        )


def _error(code: str, message: str) -> ValidationFinding:
    return ValidationFinding(code, FindingSeverity.ERROR, message)


def _warning(code: str, message: str) -> ValidationFinding:
    return ValidationFinding(code, FindingSeverity.WARNING, message)
