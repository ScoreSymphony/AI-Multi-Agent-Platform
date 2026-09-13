from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.portability import (
    CompatibilityMetadata,
    DependencyKind,
    DependencyRequirement,
    ExcludedState,
    ExclusionCategory,
    IdPolicy,
    ImportContext,
    PackageProvenance,
    PortableResource,
    ResourceExport,
    ResourceSerializerRegistry,
    build_package,
    package_from_dict,
    package_to_dict,
    seal_resource,
    verify_package,
)


def _resource(
    *,
    resource_type: str = "demo.resource",
    resource_id: str = "resource-1",
    payload: dict[str, object] | None = None,
) -> PortableResource:
    return PortableResource(
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version="1",
        payload=payload or {"name": "portable"},
    )


def test_portable_package_round_trip_preserves_manifest_and_integrity() -> None:
    dependency = DependencyRequirement(
        kind=DependencyKind.CAPABILITY,
        identifier="capability.git.read",
        version_constraint=">=1",
        purpose="read repository metadata",
    )
    resource = PortableResource(
        resource_type="agent",
        resource_id="agent-123",
        resource_version="7",
        payload={
            "name": "Researcher",
            "secret_reference": {
                "provider": "local",
                "secret_id": "secret-123",
            },
        },
        dependencies=(dependency,),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(
            source="local-export",
            author="tester",
            source_instance_id="instance-a",
        ),
        compatibility=CompatibilityMetadata(contract_versions={"agents": "1"}),
        requirements=(dependency,),
        excluded_state=(
            ExcludedState(
                category=ExclusionCategory.BACKEND_RUNTIME_STATE,
                path="agent.runtime",
                reason="runtime sessions are not portable",
                resource_type="agent",
                resource_id="agent-123",
            ),
        ),
        created_at=datetime(2026, 9, 4, 1, 2, 3, tzinfo=UTC),
    )

    document = package_to_dict(package)
    restored = package_from_dict(document)

    assert restored == package
    verify_package(restored)
    assert restored.manifest.resources[0].checksum == restored.resources[0].checksum
    assert len(restored.checksum) == 64


def test_resource_checksum_detects_payload_tampering() -> None:
    package = build_package(
        source_platform_version="0.0.1",
        resources=(_resource(),),
        provenance=PackageProvenance(source="test"),
    )
    document = package_to_dict(package)
    resources = document["resources"]
    assert isinstance(resources, list)
    resource = resources[0]
    assert isinstance(resource, dict)
    payload = resource["payload"]
    assert isinstance(payload, dict)
    payload["name"] = "tampered"

    with pytest.raises(ContractError) as exc_info:
        package_from_dict(document)

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert "resource checksum mismatch" in exc_info.value.message


def test_package_checksum_detects_manifest_tampering() -> None:
    package = build_package(
        source_platform_version="0.0.1",
        resources=(_resource(),),
        provenance=PackageProvenance(source="test"),
    )
    tampered_manifest = replace(package.manifest, source_platform_version="0.0.2")
    tampered = replace(package, manifest=tampered_manifest)

    with pytest.raises(ContractError) as exc_info:
        verify_package(tampered)

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert "package checksum mismatch" in exc_info.value.message


def test_unsupported_portable_format_version_fails_before_deserialization() -> None:
    package = build_package(
        source_platform_version="0.0.1",
        resources=(_resource(),),
        provenance=PackageProvenance(source="test"),
    )
    document = package_to_dict(package)
    manifest = document["manifest"]
    assert isinstance(manifest, dict)
    manifest["format_version"] = "99.0"

    with pytest.raises(ContractError) as exc_info:
        package_from_dict(document)

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_timezone_less_created_at_maps_to_canonical_import_error() -> None:
    package = build_package(
        source_platform_version="0.0.1",
        resources=(_resource(),),
        provenance=PackageProvenance(source="test"),
    )
    document = package_to_dict(package)
    manifest = document["manifest"]
    assert isinstance(manifest, dict)
    manifest["created_at"] = "2026-09-04T01:02:03"

    with pytest.raises(ContractError) as exc_info:
        package_from_dict(document)

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
    assert "timezone-aware" in exc_info.value.message


def test_plaintext_secret_bearing_field_is_rejected() -> None:
    with pytest.raises(ContractError) as exc_info:
        seal_resource(_resource(payload={"api_key": "plaintext-secret"}))

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
    assert "plaintext secret" in exc_info.value.message


def test_secret_reference_placeholder_is_portable() -> None:
    sealed = seal_resource(
        _resource(
            payload={
                "secret_reference": {
                    "provider": "local",
                    "secret_id": "secret-123",
                    "purpose": "repository authentication",
                }
            }
        )
    )

    assert sealed.checksum


def test_backend_private_runtime_state_is_rejected_recursively() -> None:
    with pytest.raises(ContractError) as exc_info:
        seal_resource(
            _resource(
                payload={
                    "runtime": {
                        "details": {
                            "trace_id": "private-trace",
                        }
                    }
                }
            )
        )

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
    assert exc_info.value.details["path"] == "$.runtime.details.trace_id"


class _DemoCodec:
    resource_type = "demo"

    def serialize(self, value: object) -> ResourceExport:
        assert isinstance(value, str)
        return ResourceExport(
            resource_id="demo-1",
            resource_version="1",
            payload={"value": value},
            id_policy=IdPolicy.REGENERATE,
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        target_id = context.remap(resource.resource_type, resource.resource_id)
        return (target_id, resource.payload["value"])


def test_resource_serializer_registry_round_trip_and_id_remap() -> None:
    registry = ResourceSerializerRegistry()
    registry.register(_DemoCodec())

    resource = registry.serialize("demo", "hello")
    restored = registry.deserialize(
        resource,
        ImportContext(id_mapping={("demo", "demo-1"): "demo-2"}),
    )

    assert registry.resource_types() == ("demo",)
    assert resource.id_policy is IdPolicy.REGENERATE
    assert restored == ("demo-2", "hello")


def test_resource_serializer_registry_rejects_duplicate_and_unknown_codecs() -> None:
    registry = ResourceSerializerRegistry()
    registry.register(_DemoCodec())

    with pytest.raises(ContractError) as duplicate:
        registry.register(_DemoCodec())
    assert duplicate.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as unknown:
        registry.serialize("missing", "hello")
    assert unknown.value.code is ErrorCode.NOT_FOUND
