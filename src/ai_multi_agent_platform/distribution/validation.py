"""Compatibility, integrity and risk validation for registry artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from .items import InstalledRegistryItem, RegistryItem


class FindingSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class FindingCategory(StrEnum):
    GENERAL = "general"
    DEPENDENCY = "dependency"
    COMPATIBILITY = "compatibility"
    PERMISSION = "permission"
    PROVENANCE = "provenance"
    INTEGRITY = "integrity"
    TRUST = "trust"
    POLICY = "policy"
    UPDATE = "update"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    code: str
    severity: FindingSeverity
    message: str
    category: FindingCategory = FindingCategory.GENERAL
    subject: str | None = None
    details: tuple[tuple[str, str], ...] = ()


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
    operating_system: str | None = None
    architecture: str | None = None
    available_runtimes: frozenset[str] = frozenset()


def validate_item(
    item: RegistryItem,
    artifact: bytes,
    context: ValidationContext,
) -> tuple[ValidationFinding, ...]:
    findings: list[ValidationFinding] = []
    if not item.supported_platform.contains(context.platform_version):
        findings.append(
            _error(
                "incompatible_platform",
                "platform version is not supported",
                FindingCategory.COMPATIBILITY,
                subject="platform",
            )
        )
    if item.yanked:
        findings.append(
            _error("yanked", "registry release is yanked", FindingCategory.RELEASE, item.item_id)
        )
    if item.deprecated:
        findings.append(
            _warning(
                "deprecated",
                "registry item is deprecated",
                FindingCategory.RELEASE,
                item.item_id,
            )
        )
    if item.integrity.sha256 is not None:
        actual = hashlib.sha256(artifact).hexdigest()
        if actual != item.integrity.sha256:
            findings.append(
                _error(
                    "checksum_mismatch",
                    "artifact checksum validation failed",
                    FindingCategory.INTEGRITY,
                    item.item_id,
                    (("expected", item.integrity.sha256), ("actual", actual)),
                )
            )
    if item.integrity.signature is not None:
        if context.signature_valid is False:
            findings.append(
                _error(
                    "signature_failure",
                    "artifact signature validation failed",
                    FindingCategory.INTEGRITY,
                    item.item_id,
                )
            )
        elif context.signature_valid is None:
            findings.append(
                _error(
                    "signature_unverified",
                    "signature metadata is present but no trusted verifier could validate it",
                    FindingCategory.INTEGRITY,
                    item.item_id,
                )
            )

    _validate_environment(item, context, findings)

    installed = {record.item_id: record for record in context.installed_items}
    for dependency in item.dependencies:
        record = installed.get(dependency.item_id)
        if record is None:
            if not dependency.optional:
                findings.append(
                    _error(
                        "missing_dependency",
                        f"missing {dependency.item_id}",
                        FindingCategory.DEPENDENCY,
                        dependency.item_id,
                    )
                )
            continue
        required_kind = dependency.kind_value
        installed_kind = record.kind
        if required_kind is not None:
            if installed_kind is None:
                findings.append(
                    _error(
                        "dependency_kind_unknown",
                        (
                            f"installed dependency {dependency.item_id} "
                            "has no recorded component kind"
                        ),
                        FindingCategory.DEPENDENCY,
                        dependency.item_id,
                    )
                )
            elif installed_kind != required_kind:
                findings.append(
                    _error(
                        "dependency_kind",
                        f"dependency {dependency.item_id} has incompatible component kind",
                        FindingCategory.DEPENDENCY,
                        dependency.item_id,
                        (
                            ("required_kind", required_kind),
                            ("installed_kind", installed_kind),
                        ),
                    )
                )
        if not dependency.version_range.contains(record.version):
            findings.append(
                _error(
                    "dependency_version",
                    f"incompatible dependency {dependency.item_id}",
                    FindingCategory.DEPENDENCY,
                    dependency.item_id,
                    (("installed_version", record.version),),
                )
            )

    missing_permissions = item.requested_permissions - context.grantable_permissions
    if missing_permissions:
        findings.append(
            _error(
                "permission_escalation",
                "requested permissions are not grantable: "
                + ", ".join(sorted(missing_permissions)),
                FindingCategory.PERMISSION,
                item.item_id,
                tuple(("permission", value) for value in sorted(missing_permissions)),
            )
        )
    _require_set(
        item.required_capabilities,
        context.available_capabilities,
        "capability",
        findings,
    )
    _require_set(
        frozenset(item.required_plugins),
        context.installed_plugins,
        "plugin",
        findings,
    )
    _require_set(
        frozenset(item.required_connectors),
        context.installed_connectors,
        "connector",
        findings,
    )
    _require_set(frozenset(item.required_models), context.available_models, "model", findings)

    current = installed.get(item.item_id)
    if current is not None:
        if current.license is not None and current.license != item.license:
            findings.append(
                _warning(
                    "license_changed",
                    "license metadata changed since installation",
                    FindingCategory.PROVENANCE,
                    item.item_id,
                )
            )
        if current.provenance is not None and current.provenance != item.provenance:
            findings.append(
                _warning(
                    "provenance_changed",
                    "provenance metadata changed",
                    FindingCategory.PROVENANCE,
                    item.item_id,
                )
            )
        if current.pinned_version is not None and current.pinned_version != item.version:
            findings.append(
                _error(
                    "version_pinned",
                    "installed item is pinned to another version",
                    FindingCategory.UPDATE,
                    item.item_id,
                    (("pinned_version", current.pinned_version),),
                )
            )
    if item.trust_status.value == "untrusted":
        findings.append(
            _warning(
                "untrusted",
                "registry content is explicitly untrusted",
                FindingCategory.TRUST,
                item.item_id,
            )
        )
    return tuple(findings)


def has_errors(findings: tuple[ValidationFinding, ...]) -> bool:
    return any(item.severity is FindingSeverity.ERROR for item in findings)


def _validate_environment(
    item: RegistryItem,
    context: ValidationContext,
    findings: list[ValidationFinding],
) -> None:
    compatibility = item.compatibility
    if compatibility.operating_systems:
        if context.operating_system is None:
            findings.append(
                _error(
                    "operating_system_unknown",
                    (
                        "component declares operating-system constraints "
                        "but the environment is unknown"
                    ),
                    FindingCategory.COMPATIBILITY,
                    "operating_system",
                )
            )
        elif not _contains_casefold(compatibility.operating_systems, context.operating_system):
            findings.append(
                _error(
                    "incompatible_operating_system",
                    f"operating system {context.operating_system!r} is not supported",
                    FindingCategory.COMPATIBILITY,
                    "operating_system",
                )
            )
    if compatibility.architectures:
        if context.architecture is None:
            findings.append(
                _error(
                    "architecture_unknown",
                    "component declares architecture constraints but the environment is unknown",
                    FindingCategory.COMPATIBILITY,
                    "architecture",
                )
            )
        elif not _contains_casefold(compatibility.architectures, context.architecture):
            findings.append(
                _error(
                    "incompatible_architecture",
                    f"architecture {context.architecture!r} is not supported",
                    FindingCategory.COMPATIBILITY,
                    "architecture",
                )
            )
    missing_runtimes = compatibility.required_runtimes - context.available_runtimes
    if missing_runtimes:
        findings.append(
            _error(
                "missing_runtime",
                "missing runtime: " + ", ".join(sorted(missing_runtimes)),
                FindingCategory.COMPATIBILITY,
                "runtime",
                tuple(("runtime", value) for value in sorted(missing_runtimes)),
            )
        )


def _contains_casefold(values: frozenset[str], candidate: str) -> bool:
    normalized = candidate.strip().casefold()
    return normalized in {value.strip().casefold() for value in values}


def _require_set(
    required: frozenset[str],
    available: frozenset[str],
    label: str,
    findings: list[ValidationFinding],
) -> None:
    missing = required - available
    if missing:
        findings.append(
            _error(
                f"missing_{label}",
                f"missing {label}: " + ", ".join(sorted(missing)),
                FindingCategory.COMPATIBILITY,
                label,
                tuple((label, value) for value in sorted(missing)),
            )
        )


def _error(
    code: str,
    message: str,
    category: FindingCategory = FindingCategory.GENERAL,
    subject: str | None = None,
    details: tuple[tuple[str, str], ...] = (),
) -> ValidationFinding:
    return ValidationFinding(code, FindingSeverity.ERROR, message, category, subject, details)


def _warning(
    code: str,
    message: str,
    category: FindingCategory = FindingCategory.GENERAL,
    subject: str | None = None,
    details: tuple[tuple[str, str], ...] = (),
) -> ValidationFinding:
    return ValidationFinding(code, FindingSeverity.WARNING, message, category, subject, details)
