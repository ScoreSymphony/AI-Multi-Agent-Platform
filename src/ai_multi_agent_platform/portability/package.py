"""Deterministic serialization and integrity verification for portable packages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    PORTABLE_FORMAT_VERSION,
    PORTABLE_INTEGRITY_ALGORITHM,
    CompatibilityMetadata,
    DependencyKind,
    DependencyRequirement,
    ExcludedState,
    ExclusionCategory,
    IdPolicy,
    PackageProvenance,
    PortablePackage,
    PortablePackageManifest,
    PortableResource,
    PortableResourceDescriptor,
)
from .schema import validate_package_document
from .validation import validate_portable_payload


def _canonical_json_bytes(value: JsonValue) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "portable package contains a value that is not canonical JSON",
        ) from exc


def _sha256(value: JsonValue) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _dependency_to_dict(dependency: DependencyRequirement) -> dict[str, JsonValue]:
    return {
        "kind": dependency.kind.value,
        "identifier": dependency.identifier,
        "required": dependency.required,
        "version_constraint": dependency.version_constraint,
        "purpose": dependency.purpose,
    }


def _exclusion_to_dict(exclusion: ExcludedState) -> dict[str, JsonValue]:
    return {
        "category": exclusion.category.value,
        "path": exclusion.path,
        "reason": exclusion.reason,
        "resource_type": exclusion.resource_type,
        "resource_id": exclusion.resource_id,
    }


def _resource_integrity_document(resource: PortableResource) -> dict[str, JsonValue]:
    return {
        "resource_type": resource.resource_type,
        "resource_id": resource.resource_id,
        "resource_version": resource.resource_version,
        "id_policy": resource.id_policy.value,
        "dependencies": [_dependency_to_dict(item) for item in resource.dependencies],
        "payload": dict(resource.payload),
    }


def _resource_to_dict(resource: PortableResource) -> dict[str, JsonValue]:
    document = _resource_integrity_document(resource)
    document["checksum"] = resource.checksum
    return document


def _descriptor_to_dict(descriptor: PortableResourceDescriptor) -> dict[str, JsonValue]:
    return {
        "resource_type": descriptor.resource_type,
        "resource_id": descriptor.resource_id,
        "resource_version": descriptor.resource_version,
        "id_policy": descriptor.id_policy.value,
        "checksum": descriptor.checksum,
        "dependencies": [_dependency_to_dict(item) for item in descriptor.dependencies],
    }


def _manifest_to_dict(manifest: PortablePackageManifest) -> dict[str, JsonValue]:
    return {
        "format_version": manifest.format_version,
        "source_platform_version": manifest.source_platform_version,
        "created_at": manifest.created_at.isoformat(),
        "integrity_algorithm": manifest.integrity_algorithm,
        "resources": [_descriptor_to_dict(item) for item in manifest.resources],
        "requirements": [_dependency_to_dict(item) for item in manifest.requirements],
        "provenance": {
            "source": manifest.provenance.source,
            "author": manifest.provenance.author,
            "source_instance_id": manifest.provenance.source_instance_id,
            "metadata": dict(manifest.provenance.metadata),
        },
        "compatibility": {
            "minimum_platform_version": manifest.compatibility.minimum_platform_version,
            "maximum_platform_version": manifest.compatibility.maximum_platform_version,
            "contract_versions": dict(manifest.compatibility.contract_versions),
        },
        "excluded_state": [_exclusion_to_dict(item) for item in manifest.excluded_state],
    }


def _package_integrity_document(
    manifest: PortablePackageManifest,
    resources: tuple[PortableResource, ...],
) -> dict[str, JsonValue]:
    return {
        "manifest": _manifest_to_dict(manifest),
        "resources": [_resource_to_dict(item) for item in resources],
    }


def seal_resource(resource: PortableResource) -> PortableResource:
    """Validate and attach the deterministic checksum for one portable resource."""

    validate_portable_payload(resource.payload)
    checksum = _sha256(_resource_integrity_document(resource))
    return replace(resource, checksum=checksum)


def verify_resource(resource: PortableResource) -> None:
    """Verify safety and integrity for one already sealed resource."""

    validate_portable_payload(resource.payload)
    expected = _sha256(_resource_integrity_document(resource))
    if resource.checksum != expected:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable resource checksum mismatch",
            details={
                "resource_type": resource.resource_type,
                "resource_id": resource.resource_id,
                "resource_version": resource.resource_version,
            },
        )


def build_package(
    *,
    source_platform_version: str,
    resources: tuple[PortableResource, ...],
    provenance: PackageProvenance,
    compatibility: CompatibilityMetadata | None = None,
    requirements: tuple[DependencyRequirement, ...] = (),
    excluded_state: tuple[ExcludedState, ...] = (),
    created_at: datetime | None = None,
) -> PortablePackage:
    """Build a deterministic integrity-sealed portable package."""

    sealed_resources = tuple(seal_resource(resource) for resource in resources)
    manifest_kwargs: dict[str, Any] = {
        "source_platform_version": source_platform_version,
        "resources": tuple(
            PortableResourceDescriptor.from_resource(resource) for resource in sealed_resources
        ),
        "provenance": provenance,
        "compatibility": compatibility or CompatibilityMetadata(),
        "requirements": requirements,
        "excluded_state": excluded_state,
    }
    if created_at is not None:
        manifest_kwargs["created_at"] = created_at
    manifest = PortablePackageManifest(**manifest_kwargs)
    validate_portable_payload(manifest.provenance.metadata)
    checksum = _sha256(_package_integrity_document(manifest, sealed_resources))
    return PortablePackage(manifest=manifest, resources=sealed_resources, checksum=checksum)


def verify_package(package: PortablePackage) -> None:
    """Validate format, safety, descriptor binding and all integrity digests."""

    manifest = package.manifest
    if manifest.format_version != PORTABLE_FORMAT_VERSION:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"unsupported portable package format version: {manifest.format_version}",
            details={"supported_format_version": PORTABLE_FORMAT_VERSION},
        )
    if manifest.integrity_algorithm != PORTABLE_INTEGRITY_ALGORITHM:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"unsupported portable integrity algorithm: {manifest.integrity_algorithm}",
            details={"supported_integrity_algorithm": PORTABLE_INTEGRITY_ALGORITHM},
        )

    validate_portable_payload(manifest.provenance.metadata)
    descriptors = {descriptor.identity: descriptor for descriptor in manifest.resources}
    resources = {resource.identity: resource for resource in package.resources}
    if descriptors.keys() != resources.keys():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable manifest resource inventory does not match package resources",
        )

    for identity, resource in resources.items():
        verify_resource(resource)
        descriptor = descriptors[identity]
        if (
            descriptor.checksum != resource.checksum
            or descriptor.id_policy != resource.id_policy
            or descriptor.dependencies != resource.dependencies
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable manifest descriptor does not match resource",
                details={
                    "resource_type": resource.resource_type,
                    "resource_id": resource.resource_id,
                    "resource_version": resource.resource_version,
                },
            )

    expected = _sha256(_package_integrity_document(manifest, package.resources))
    if package.checksum != expected:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable package checksum mismatch",
        )


def package_to_dict(package: PortablePackage) -> dict[str, JsonValue]:
    """Serialize a portable package into the versioned public JSON shape."""

    return {
        "manifest": _manifest_to_dict(package.manifest),
        "resources": [_resource_to_dict(item) for item in package.resources],
        "checksum": package.checksum,
    }


def _dependency_from_dict(document: dict[str, Any]) -> DependencyRequirement:
    return DependencyRequirement(
        kind=DependencyKind(document["kind"]),
        identifier=cast(str, document["identifier"]),
        required=cast(bool, document["required"]),
        version_constraint=cast(str | None, document["version_constraint"]),
        purpose=cast(str | None, document["purpose"]),
    )


def _resource_from_dict(document: dict[str, Any]) -> PortableResource:
    return PortableResource(
        resource_type=cast(str, document["resource_type"]),
        resource_id=cast(str, document["resource_id"]),
        resource_version=cast(str, document["resource_version"]),
        id_policy=IdPolicy(document["id_policy"]),
        dependencies=tuple(
            _dependency_from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], document["dependencies"])
        ),
        payload=cast(dict[str, JsonValue], document["payload"]),
        checksum=cast(str, document["checksum"]),
    )


def _descriptor_from_dict(document: dict[str, Any]) -> PortableResourceDescriptor:
    return PortableResourceDescriptor(
        resource_type=cast(str, document["resource_type"]),
        resource_id=cast(str, document["resource_id"]),
        resource_version=cast(str, document["resource_version"]),
        id_policy=IdPolicy(document["id_policy"]),
        checksum=cast(str, document["checksum"]),
        dependencies=tuple(
            _dependency_from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], document["dependencies"])
        ),
    )


def _exclusion_from_dict(document: dict[str, Any]) -> ExcludedState:
    return ExcludedState(
        category=ExclusionCategory(document["category"]),
        path=cast(str, document["path"]),
        reason=cast(str, document["reason"]),
        resource_type=cast(str | None, document["resource_type"]),
        resource_id=cast(str | None, document["resource_id"]),
    )


def package_from_dict(document: object) -> PortablePackage:
    """Validate, deserialize and integrity-check an imported package document."""

    validate_package_document(document)
    root = cast(dict[str, Any], document)
    manifest_document = cast(dict[str, Any], root["manifest"])
    provenance_document = cast(dict[str, Any], manifest_document["provenance"])
    compatibility_document = cast(dict[str, Any], manifest_document["compatibility"])

    created_at_raw = cast(str, manifest_document["created_at"])
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable package created_at is not a valid date-time",
        ) from exc
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable package created_at must be timezone-aware",
        )

    manifest = PortablePackageManifest(
        format_version=cast(str, manifest_document["format_version"]),
        source_platform_version=cast(str, manifest_document["source_platform_version"]),
        created_at=created_at,
        integrity_algorithm=cast(str, manifest_document["integrity_algorithm"]),
        resources=tuple(
            _descriptor_from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], manifest_document["resources"])
        ),
        requirements=tuple(
            _dependency_from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], manifest_document["requirements"])
        ),
        provenance=PackageProvenance(
            source=cast(str, provenance_document["source"]),
            author=cast(str | None, provenance_document["author"]),
            source_instance_id=cast(str | None, provenance_document["source_instance_id"]),
            metadata=cast(dict[str, JsonValue], provenance_document["metadata"]),
        ),
        compatibility=CompatibilityMetadata(
            minimum_platform_version=cast(
                str | None,
                compatibility_document["minimum_platform_version"],
            ),
            maximum_platform_version=cast(
                str | None,
                compatibility_document["maximum_platform_version"],
            ),
            contract_versions=cast(
                dict[str, str],
                compatibility_document["contract_versions"],
            ),
        ),
        excluded_state=tuple(
            _exclusion_from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], manifest_document["excluded_state"])
        ),
    )
    package = PortablePackage(
        manifest=manifest,
        resources=tuple(
            _resource_from_dict(cast(dict[str, Any], item))
            for item in cast(list[Any], root["resources"])
        ),
        checksum=cast(str, root["checksum"]),
    )
    verify_package(package)
    return package
