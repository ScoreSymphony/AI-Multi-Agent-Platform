from __future__ import annotations

import hashlib

import pytest

from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    DistributionService,
    LocalRegistryProvider,
    RegistryDependency,
    RegistryItem,
    RegistryItemType,
    RegistryQuery,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    VersionRange,
    validate_item,
)


def _item(payload: bytes = b"safe") -> RegistryItem:
    return RegistryItem(
        item_id="example.plugin",
        item_type=RegistryItemType.PLUGIN,
        name="Example",
        description="Reference registry item",
        version="1.2.0",
        publisher="example",
        source=RegistrySource("https://example.invalid/repo", "example.plugin@1.2.0"),
        license="MIT",
        provenance="source-release",
        supported_platform=VersionRange("0.0.1", "1.0.0"),
        requested_permissions=frozenset({"network_access"}),
        tags=frozenset({"reference", "tooling"}),
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(payload).hexdigest()),
        trust_status=TrustStatus.UNTRUSTED,
    )


def test_registry_can_be_disabled_without_affecting_core_construction() -> None:
    service = DistributionService(None)
    assert service.enabled is False
    with pytest.raises(RuntimeError, match="registry is disabled"):
        service.preview("example.plugin", "1.2.0", ValidationContext("0.0.1"))


def test_local_catalog_discovery_is_offline_and_filterable() -> None:
    item = _item()
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): b"safe"})
    results = provider.search(
        RegistryQuery(
            text="example",
            item_types=frozenset({RegistryItemType.PLUGIN}),
            tags=frozenset({"reference"}),
            platform_version="0.0.1",
        )
    )
    assert results == (item,)


def test_compatible_artifact_has_no_validation_errors() -> None:
    findings = validate_item(
        _item(),
        b"safe",
        ValidationContext("0.0.1", grantable_permissions=frozenset({"network_access"})),
    )
    assert not [finding for finding in findings if finding.severity.value == "error"]
    assert any(finding.code == "untrusted" for finding in findings)


def test_incompatible_platform_is_rejected() -> None:
    findings = validate_item(
        _item(),
        b"safe",
        ValidationContext("2.0.0", grantable_permissions=frozenset({"network_access"})),
    )
    assert any(finding.code == "incompatible_platform" for finding in findings)


def test_missing_dependency_is_rejected() -> None:
    base = _item()
    item = RegistryItem(
        **{
            field: getattr(base, field)
            for field in base.__dataclass_fields__
            if field != "dependencies"
        },
        dependencies=(RegistryDependency("required.plugin"),),
    )
    findings = validate_item(
        item,
        b"safe",
        ValidationContext("0.0.1", grantable_permissions=frozenset({"network_access"})),
    )
    assert any(finding.code == "missing_dependency" for finding in findings)


def test_permission_escalation_is_rejected() -> None:
    findings = validate_item(_item(), b"safe", ValidationContext("0.0.1"))
    assert any(finding.code == "permission_escalation" for finding in findings)


def test_checksum_failure_is_rejected() -> None:
    findings = validate_item(
        _item(),
        b"tampered",
        ValidationContext("0.0.1", grantable_permissions=frozenset({"network_access"})),
    )
    assert any(finding.code == "checksum_mismatch" for finding in findings)
