from __future__ import annotations

import hashlib

from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    VersionRange,
    validate_item,
)


def test_declared_signature_failure_blocks_activation() -> None:
    payload = b"signed-artifact"
    item = RegistryItem(
        item_id="example.signed",
        item_type=RegistryItemType.PLUGIN,
        name="Signed example",
        description="Signature validation fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource("https://example.invalid/repo", "signed@1.0.0"),
        license="MIT",
        provenance="signed-release",
        supported_platform=VersionRange("0.0.1", "1.0.0"),
        integrity=ArtifactIntegrity(
            sha256=hashlib.sha256(payload).hexdigest(),
            signature="detached-signature",
            signature_key_id="publisher-key",
        ),
        trust_status=TrustStatus.REVIEWED,
    )
    findings = validate_item(item, payload, ValidationContext("0.0.1", signature_valid=False))
    assert any(finding.code == "signature_failure" for finding in findings)
