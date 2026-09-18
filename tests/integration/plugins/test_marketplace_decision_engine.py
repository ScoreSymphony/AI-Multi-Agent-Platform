from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    DependencyStatus,
    DistributionService,
    FindingCategory,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MultiRegistryProvider,
    RegistryCompatibility,
    RegistryDependency,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistryQuery,
    RegistrySignatureVerifier,
    RegistrySource,
    RegistrySourceConflictError,
    TrustStatus,
    ValidationContext,
    VersionRange,
    registry_item_from_document,
)


class _Router:
    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        return item.item_id, artifact

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        return item.item_id, artifact


class _RejectingSignatureVerifier:
    def verify(self, item: RegistryItem, artifact: bytes) -> bool | None:
        del item, artifact
        return False


class _AcceptingSignatureVerifier:
    def verify(self, item: RegistryItem, artifact: bytes) -> bool | None:
        del item, artifact
        return True


def _item(
    item_id: str,
    item_type: RegistryItemType | str = RegistryItemType.TEMPLATE,
    *,
    version: str = "1.0.0",
    payload: bytes | None = None,
    publisher: str = "example",
    repository: str | None = None,
    dependencies: tuple[RegistryDependency, ...] = (),
    requested_permissions: frozenset[str] = frozenset(),
    supported_platform: VersionRange | None = None,
    compatibility: RegistryCompatibility | None = None,
    trust_status: TrustStatus = TrustStatus.REVIEWED,
    deprecated: bool = False,
    yanked: bool = False,
    signature: str | None = None,
    signature_key_id: str | None = None,
    declared_sha256: str | None = None,
) -> tuple[RegistryItem, bytes]:
    artifact = payload if payload is not None else f"{item_id}:{version}".encode()
    kind = item_type.value if isinstance(item_type, RegistryItemType) else item_type
    manifest = None
    if not isinstance(item_type, RegistryItemType):
        manifest = RegistryManifestReference(
            kind=kind,
            reference=f"manifests/{item_id}.json",
            schema_version="1",
        )
    return (
        RegistryItem(
            item_id=item_id,
            item_type=item_type,
            name=item_id,
            description=f"Marketplace fixture for {item_id}",
            version=version,
            publisher=publisher,
            source=RegistrySource(
                repository or f"https://example.invalid/{item_id}",
                f"{item_id}@{version}",
                revision=f"rev-{version}",
            ),
            license="MIT",
            provenance="test-registry-release",
            supported_platform=supported_platform or VersionRange("0.0.1", "1.0.0"),
            dependencies=dependencies,
            requested_permissions=requested_permissions,
            compatibility=compatibility or RegistryCompatibility(),
            integrity=ArtifactIntegrity(
                sha256=declared_sha256 or hashlib.sha256(artifact).hexdigest(),
                signature=signature,
                signature_key_id=signature_key_id,
            ),
            trust_status=trust_status,
            deprecated=deprecated,
            yanked=yanked,
            manifest=manifest,
        ),
        artifact,
    )


def _service(
    items: tuple[tuple[RegistryItem, bytes], ...],
    *,
    store: JsonRegistryInstallationStore | None = None,
    provider_id: str = "local",
    signature_verifier: RegistrySignatureVerifier | None = None,
) -> DistributionService:
    provider = LocalRegistryProvider(
        tuple(item for item, _artifact in items),
        {(item.item_id, item.version): artifact for item, artifact in items},
        provider_id=provider_id,
    )
    return DistributionService(
        provider,
        _Router(),
        installations=store,
        signature_verifier=signature_verifier,
    )


def _context(
    *,
    permissions: frozenset[str] = frozenset(),
    operating_system: str | None = None,
    architecture: str | None = None,
    runtimes: frozenset[str] = frozenset(),
) -> ValidationContext:
    return ValidationContext(
        "0.0.1",
        grantable_permissions=permissions,
        operating_system=operating_system,
        architecture=architecture,
        available_runtimes=runtimes,
    )


def test_cross_kind_dependency_is_satisfied_by_installed_tool(tmp_path: Path) -> None:
    tool, tool_artifact = _item("example.tool", RegistryItemType.TOOL)
    workflow, workflow_artifact = _item(
        "example.workflow",
        RegistryItemType.WORKFLOW,
        dependencies=(
            RegistryDependency(
                tool.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(tool, provider_id="local")
    service = _service(
        ((tool, tool_artifact), (workflow, workflow_artifact)),
        store=store,
    )

    preview = service.preview(workflow.item_id, workflow.version, _context())

    assert preview.activation_allowed is True
    assert preview.decision.dependencies[0].status is DependencyStatus.SATISFIED
    assert preview.decision.dependencies[0].item_kind == "tool"


def test_missing_and_optional_dependencies_are_distinguished() -> None:
    missing, missing_artifact = _item(
        "example.required",
        dependencies=(RegistryDependency("missing.tool"),),
    )
    optional, optional_artifact = _item(
        "example.optional",
        dependencies=(RegistryDependency("missing.tool", optional=True),),
    )
    service = _service(
        ((missing, missing_artifact), (optional, optional_artifact)),
    )

    required_preview = service.preview(missing.item_id, missing.version, _context())
    optional_preview = service.preview(optional.item_id, optional.version, _context())

    assert required_preview.activation_allowed is False
    assert any(finding.code == "missing_dependency" for finding in required_preview.findings)
    assert optional_preview.activation_allowed is True
    optional_finding = next(
        finding
        for finding in optional_preview.findings
        if finding.code.startswith("optional_dependency_")
    )
    assert optional_finding.severity.value == "warning"
    assert optional_finding.category is FindingCategory.DEPENDENCY


def test_dependency_cycle_is_explained() -> None:
    first, first_artifact = _item(
        "example.first",
        dependencies=(RegistryDependency("example.second"),),
    )
    second, second_artifact = _item(
        "example.second",
        dependencies=(RegistryDependency("example.first"),),
    )
    service = _service(((first, first_artifact), (second, second_artifact)))

    preview = service.preview(first.item_id, first.version, _context())

    cycle = next(finding for finding in preview.findings if finding.code == "dependency_cycle")
    assert preview.activation_allowed is False
    assert cycle.subject == first.item_id
    assert "example.first -> example.second -> example.first" in cycle.message


def test_conflicting_transitive_dependency_constraints_are_rejected() -> None:
    low, low_artifact = _item("example.shared", version="1.0.0")
    high, high_artifact = _item("example.shared", version="2.0.0")
    left, left_artifact = _item(
        "example.left",
        dependencies=(
            RegistryDependency(
                "example.shared",
                VersionRange(maximum="1.0.0"),
            ),
        ),
    )
    right, right_artifact = _item(
        "example.right",
        dependencies=(
            RegistryDependency(
                "example.shared",
                VersionRange(minimum="2.0.0"),
            ),
        ),
    )
    root, root_artifact = _item(
        "example.root",
        dependencies=(
            RegistryDependency(left.item_id),
            RegistryDependency(right.item_id),
        ),
    )
    service = _service(
        (
            (low, low_artifact),
            (high, high_artifact),
            (left, left_artifact),
            (right, right_artifact),
            (root, root_artifact),
        )
    )

    preview = service.preview(root.item_id, root.version, _context())

    conflicts = [
        dependency
        for dependency in preview.decision.dependencies
        if dependency.item_id == "example.shared"
        and dependency.status is DependencyStatus.VERSION_CONFLICT
    ]
    assert conflicts
    assert any(finding.code == "dependency_version" for finding in preview.findings)


def test_platform_os_architecture_and_runtime_incompatibility_is_typed() -> None:
    item, artifact = _item(
        "example.environment",
        supported_platform=VersionRange("2.0.0", "3.0.0"),
        compatibility=RegistryCompatibility(
            operating_systems=frozenset({"linux"}),
            architectures=frozenset({"x86_64"}),
            required_runtimes=frozenset({"docker"}),
        ),
    )
    service = _service(((item, artifact),))

    preview = service.preview(
        item.item_id,
        item.version,
        _context(
            operating_system="windows",
            architecture="arm64",
        ),
    )

    assert preview.activation_allowed is False
    assert preview.decision.compatibility.compatible is False
    assert preview.decision.compatibility.platform_compatible is False
    assert preview.decision.compatibility.operating_system_compatible is False
    assert preview.decision.compatibility.architecture_compatible is False
    assert preview.decision.compatibility.missing_runtimes == ("docker",)
    codes = {finding.code for finding in preview.findings}
    assert {
        "incompatible_platform",
        "incompatible_operating_system",
        "incompatible_architecture",
        "missing_runtime",
    } <= codes


def test_update_permission_escalation_requires_review(tmp_path: Path) -> None:
    old, old_artifact = _item(
        "example.permissions",
        requested_permissions=frozenset({"filesystem.read"}),
    )
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        requested_permissions=frozenset({"filesystem.read", "filesystem.write"}),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(
        candidate.item_id,
        candidate.version,
        _context(permissions=candidate.requested_permissions),
    )

    diff = preview.decision.permission_diff
    assert diff.added == ("filesystem.write",)
    assert diff.removed == ()
    assert diff.unchanged == ("filesystem.read",)
    assert preview.decision.approval.required is True
    assert "new_permissions" in preview.decision.approval.reasons
    assert any(finding.code == "permission_added" for finding in preview.findings)


def test_update_permission_reduction_is_visible_without_escalation(tmp_path: Path) -> None:
    old, old_artifact = _item(
        "example.permission-reduction",
        requested_permissions=frozenset({"filesystem.read", "filesystem.write"}),
    )
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        requested_permissions=frozenset({"filesystem.read"}),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(
        candidate.item_id,
        candidate.version,
        _context(permissions=frozenset({"filesystem.read"})),
    )

    diff = preview.decision.permission_diff
    assert diff.added == ()
    assert diff.removed == ("filesystem.write",)
    assert diff.unchanged == ("filesystem.read",)
    assert "new_permissions" not in preview.decision.approval.reasons
    assert preview.decision.update_state.permission_change is True


def test_integrity_and_signature_mismatches_block_preview() -> None:
    declared = hashlib.sha256(b"expected").hexdigest()
    bad_hash, bad_hash_artifact = _item(
        "example.bad-hash",
        payload=b"tampered",
        declared_sha256=declared,
    )
    signed, signed_artifact = _item(
        "example.bad-signature",
        signature="test-signature",
        signature_key_id="publisher-key",
    )

    integrity_preview = _service(((bad_hash, bad_hash_artifact),)).preview(
        bad_hash.item_id,
        bad_hash.version,
        _context(),
    )
    signature_preview = _service(
        ((signed, signed_artifact),),
        signature_verifier=_RejectingSignatureVerifier(),
    ).preview(
        signed.item_id,
        signed.version,
        _context(),
    )

    assert integrity_preview.activation_allowed is False
    assert any(
        finding.code == "checksum_mismatch" and finding.category is FindingCategory.INTEGRITY
        for finding in integrity_preview.findings
    )
    assert signature_preview.activation_allowed is False
    assert any(
        finding.code == "signature_failure" and finding.category is FindingCategory.INTEGRITY
        for finding in signature_preview.findings
    )


def test_source_change_is_explicit_and_requires_review(tmp_path: Path) -> None:
    old, old_artifact = _item("example.source-change", version="1.0.0")
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        repository="https://other.invalid/source-change",
    )
    official = LocalRegistryProvider(
        (old,),
        {(old.item_id, old.version): old_artifact},
        provider_id="official",
    )
    community = LocalRegistryProvider(
        (candidate,),
        {(candidate.item_id, candidate.version): candidate_artifact},
        provider_id="community",
    )
    provider = MultiRegistryProvider((official, community))
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(
        replace(old, source_registry="official"),
        provider_id="official",
    )
    service = DistributionService(provider, _Router(), installations=store)

    preview = service.preview(
        candidate.item_id,
        candidate.version,
        _context(),
        source_registry="community",
    )

    provenance = preview.decision.provenance_diff
    assert provenance.source_changed is True
    assert provenance.repository_changed is True
    assert preview.decision.update_state.source_change is True
    assert preview.decision.approval.required is True
    assert {"source_change", "repository_change"} <= set(preview.decision.approval.reasons)


def test_publisher_change_is_explicit_and_requires_review(tmp_path: Path) -> None:
    old, old_artifact = _item("example.publisher-change")
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        publisher="other-publisher",
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    assert preview.decision.provenance_diff.publisher_changed is True
    assert "publisher_change" in preview.decision.approval.reasons
    assert any(finding.code == "publisher_changed" for finding in preview.findings)


def test_pin_blocks_candidate_but_update_state_remains_explainable(
    tmp_path: Path,
) -> None:
    old, old_artifact = _item("example.pin")
    candidate, candidate_artifact = _item(old.item_id, version="1.1.0")
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    store.pin(old.item_id, old.version)
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    state = preview.decision.update_state
    assert state.installed_version == old.version
    assert state.update_available is True
    assert state.pinned is True
    assert state.blocked_by_pin is True
    assert preview.activation_allowed is False
    assert any(finding.code == "version_pinned" for finding in preview.findings)


def test_yanked_and_deprecated_candidates_have_distinct_update_state(
    tmp_path: Path,
) -> None:
    old, old_artifact = _item("example.release-state")
    deprecated, deprecated_artifact = _item(
        old.item_id,
        version="1.1.0",
        deprecated=True,
    )
    yanked, yanked_artifact = _item(
        old.item_id,
        version="1.2.0",
        yanked=True,
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        (
            (old, old_artifact),
            (deprecated, deprecated_artifact),
            (yanked, yanked_artifact),
        ),
        store=store,
    )

    deprecated_preview = service.preview(
        deprecated.item_id,
        deprecated.version,
        _context(),
    )
    yanked_preview = service.preview(yanked.item_id, yanked.version, _context())

    assert deprecated_preview.activation_allowed is True
    assert deprecated_preview.decision.update_state.candidate_deprecated is True
    assert deprecated_preview.decision.update_state.latest_compatible_version == "1.1.0"
    assert any(finding.code == "deprecated" for finding in deprecated_preview.findings)
    assert yanked_preview.activation_allowed is False
    assert yanked_preview.decision.update_state.candidate_yanked is True
    assert any(finding.code == "yanked" for finding in yanked_preview.findings)


def test_multiple_sources_do_not_silently_collapse_identity() -> None:
    first, first_artifact = _item("example.duplicate")
    second, second_artifact = _item("example.duplicate")
    official = LocalRegistryProvider(
        (first,),
        {(first.item_id, first.version): first_artifact},
        provider_id="official",
    )
    private = LocalRegistryProvider(
        (second,),
        {(second.item_id, second.version): second_artifact},
        provider_id="private",
    )
    provider = MultiRegistryProvider((official, private))

    discovered = provider.search(RegistryQuery())

    assert [(item.item_id, item.source_registry) for item in discovered] == [
        ("example.duplicate", "official"),
        ("example.duplicate", "private"),
    ]
    with pytest.raises(RegistrySourceConflictError):
        provider.get("example.duplicate", "1.0.0")
    assert (
        provider.get_from_source("private", "example.duplicate", "1.0.0").source_registry
        == "private"
    )


def test_future_component_kind_participates_in_cross_kind_dependencies(
    tmp_path: Path,
) -> None:
    future, future_artifact = _item(
        "example.notebook",
        "notebook_extension",
    )
    root, root_artifact = _item(
        "example.future-dependent",
        dependencies=(
            RegistryDependency(
                future.item_id,
                item_kind="notebook_extension",
            ),
        ),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(future, provider_id="local")
    service = _service(
        ((future, future_artifact), (root, root_artifact)),
        store=store,
    )

    preview = service.preview(root.item_id, root.version, _context())

    assert preview.activation_allowed is True
    dependency = preview.decision.dependencies[0]
    assert dependency.status is DependencyStatus.SATISFIED
    assert dependency.item_kind == "notebook_extension"


def test_preview_findings_are_structured_and_explainable() -> None:
    item, artifact = _item(
        "example.explainable",
        dependencies=(RegistryDependency("example.missing", item_kind=RegistryItemType.TOOL),),
    )
    preview = _service(((item, artifact),)).preview(
        item.item_id,
        item.version,
        _context(),
    )

    finding = next(finding for finding in preview.findings if finding.code == "missing_dependency")
    assert finding.category is FindingCategory.DEPENDENCY
    assert finding.subject == "example.missing"
    assert ("required_by", item.item_id) in finding.details
    assert ("required_kind", "tool") in finding.details
    assert preview.decision.dependency_blocked is True


def test_uninstall_preview_blocks_required_installed_dependents(
    tmp_path: Path,
) -> None:
    tool, tool_artifact = _item("example.uninstall-tool", RegistryItemType.TOOL)
    workflow, workflow_artifact = _item(
        "example.uninstall-workflow",
        RegistryItemType.WORKFLOW,
        dependencies=(
            RegistryDependency(
                tool.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(tool, provider_id="local")
    store.record(workflow, provider_id="local")
    service = _service(
        ((tool, tool_artifact), (workflow, workflow_artifact)),
        store=store,
    )

    preview = service.preview_uninstall(tool.item_id)

    assert preview.activation_allowed is False
    assert preview.decision.operation.value == "uninstall"
    assert preview.decision.dependencies[0].status is DependencyStatus.REQUIRED_BY_INSTALLED
    assert any(finding.code == "required_by_installed" for finding in preview.findings)


def test_self_dependency_is_rejected_explicitly() -> None:
    item, artifact = _item(
        "example.self-dependent",
        dependencies=(RegistryDependency("example.self-dependent"),),
    )

    preview = _service(((item, artifact),)).preview(
        item.item_id,
        item.version,
        _context(),
    )

    assert preview.activation_allowed is False
    assert preview.decision.dependencies[0].status is DependencyStatus.SELF_DEPENDENCY
    assert any(finding.code == "self_dependency" for finding in preview.findings)


def test_incompatible_update_state_keeps_latest_compatible_release(
    tmp_path: Path,
) -> None:
    old, old_artifact = _item("example.incompatible-update")
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        supported_platform=VersionRange("2.0.0", "3.0.0"),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    state = preview.decision.update_state
    assert state.update_available is True
    assert state.incompatible_update is True
    assert state.latest_compatible_version == old.version
    assert preview.activation_allowed is False


def test_signature_key_change_is_visible_and_requires_security_review(
    tmp_path: Path,
) -> None:
    old, old_artifact = _item(
        "example.signature-key",
        signature="signature-v1",
        signature_key_id="publisher-key-v1",
    )
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        signature="signature-v2",
        signature_key_id="publisher-key-v2",
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
        signature_verifier=_AcceptingSignatureVerifier(),
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    provenance = preview.decision.provenance_diff
    assert provenance.signature_changed is True
    assert provenance.signature_key_changed is True
    assert "signature_key_change" in preview.decision.approval.reasons
    assert "signature_change" not in preview.decision.approval.reasons
    assert preview.decision.approval.authorization_required is True


def test_schema_v4_round_trips_cross_kind_and_environment_constraints() -> None:
    document = {
        "schema_version": "4",
        "item_id": "example.schema-v4",
        "item_type": "workflow",
        "name": "Schema v4",
        "description": "Schema v4 compatibility fixture",
        "version": "1.0.0",
        "publisher": "example",
        "source": {
            "repository": "https://example.invalid/schema-v4",
            "package_reference": "example.schema-v4@1.0.0",
            "revision": "rev-1",
        },
        "license": "MIT",
        "provenance": "test",
        "supported_platform": {"minimum": "0.0.1", "maximum": "1.0.0"},
        "dependencies": [
            {
                "item_id": "example.future-tool",
                "item_kind": "future_tool",
                "version_range": {"minimum": "1.0.0", "maximum": "2.0.0"},
                "optional": False,
            }
        ],
        "requested_permissions": [],
        "required_capabilities": [],
        "required_plugins": [],
        "required_connectors": [],
        "required_models": [],
        "tags": [],
        "categories": [],
        "integrity": {
            "sha256": None,
            "signature": None,
            "signature_key_id": None,
        },
        "trust_status": "reviewed",
        "review_reference": None,
        "released_at": None,
        "changelog": None,
        "deprecated": False,
        "yanked": False,
        "compatibility": {
            "operating_systems": ["linux"],
            "architectures": ["x86_64"],
            "required_runtimes": ["python"],
        },
    }

    item = registry_item_from_document(document)

    assert item.dependencies[0].kind_value == "future_tool"
    assert item.compatibility.operating_systems == frozenset({"linux"})
    assert item.compatibility.architectures == frozenset({"x86_64"})
    assert item.compatibility.required_runtimes == frozenset({"python"})


def test_current_yanked_release_is_reported_in_update_state(
    tmp_path: Path,
) -> None:
    current, current_artifact = _item(
        "example.current-yanked",
        yanked=True,
    )
    candidate, candidate_artifact = _item(
        current.item_id,
        version="1.1.0",
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(
        replace(current, yanked=False),
        provider_id="local",
    )
    service = _service(
        ((current, current_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    assert preview.decision.update_state.current_yanked is True
    assert preview.decision.update_state.candidate_yanked is False
    assert preview.decision.update_state.latest_compatible_version == candidate.version


def test_trust_downgrade_is_visible_and_requires_review(
    tmp_path: Path,
) -> None:
    old, old_artifact = _item(
        "example.trust-downgrade",
        trust_status=TrustStatus.TRUSTED,
    )
    candidate, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
        trust_status=TrustStatus.UNTRUSTED,
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    assert preview.decision.provenance_diff.trust_downgraded is True
    assert preview.decision.update_state.trust_integrity_issue is True
    assert "trust_downgrade" in preview.decision.approval.reasons
    assert any(finding.code == "trust_downgrade" for finding in preview.findings)


def test_dependency_from_multiple_sources_requires_explicit_source_choice() -> None:
    root, root_artifact = _item(
        "example.ambiguous-root",
        dependencies=(
            RegistryDependency(
                "example.ambiguous-tool",
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    tool_a, tool_a_artifact = _item(
        "example.ambiguous-tool",
        RegistryItemType.TOOL,
    )
    tool_b, tool_b_artifact = _item(
        "example.ambiguous-tool",
        RegistryItemType.TOOL,
    )
    root_provider = LocalRegistryProvider(
        (root,),
        {(root.item_id, root.version): root_artifact},
        provider_id="root-source",
    )
    source_a = LocalRegistryProvider(
        (tool_a,),
        {(tool_a.item_id, tool_a.version): tool_a_artifact},
        provider_id="source-a",
    )
    source_b = LocalRegistryProvider(
        (tool_b,),
        {(tool_b.item_id, tool_b.version): tool_b_artifact},
        provider_id="source-b",
    )
    service = DistributionService(
        MultiRegistryProvider((root_provider, source_a, source_b)),
        _Router(),
    )

    preview = service.preview(
        root.item_id,
        root.version,
        _context(),
        source_registry="root-source",
    )

    dependency = next(
        item for item in preview.decision.dependencies if item.item_id == "example.ambiguous-tool"
    )
    assert dependency.status is DependencyStatus.SOURCE_AMBIGUOUS
    assert preview.activation_allowed is False
    assert any(finding.code == "dependency_source_ambiguous" for finding in preview.findings)


def test_installed_dependency_does_not_inherit_newer_catalog_transitive_requirements(
    tmp_path: Path,
) -> None:
    installed, _installed_artifact = _item(
        "example.installed-dependency",
        RegistryItemType.TOOL,
        version="1.0.0",
    )
    newer, newer_artifact = _item(
        installed.item_id,
        RegistryItemType.TOOL,
        version="2.0.0",
        dependencies=(RegistryDependency("example.new-transitive-dependency"),),
    )
    root, root_artifact = _item(
        "example.installed-dependent-root",
        dependencies=(
            RegistryDependency(
                installed.item_id,
                VersionRange("1.0.0", "2.0.0"),
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(installed, provider_id="local")
    service = _service(
        ((newer, newer_artifact), (root, root_artifact)),
        store=store,
    )

    preview = service.preview(root.item_id, root.version, _context())

    assert preview.activation_allowed is True
    assert preview.decision.dependencies[0].status is DependencyStatus.SATISFIED
    assert all(
        dependency.item_id != "example.new-transitive-dependency"
        for dependency in preview.decision.dependencies
    )
