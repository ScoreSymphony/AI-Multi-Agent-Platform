from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    DependencyStatus,
    DistributionService,
    FindingCategory,
    InstalledRegistryItem,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindHandlerRegistry,
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


class _RecordingRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(("plugin", item.item_id))
        return item.item_id

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(("portable", item.item_id))
        return item.item_id


class _ChangingArtifactProvider:
    def __init__(self, item: RegistryItem, artifact: bytes) -> None:
        self._delegate = LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): artifact},
            provider_id="local",
        )
        self._artifact = artifact
        self.fetch_count = 0

    @property
    def provider_id(self) -> str:
        return self._delegate.provider_id

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        return self._delegate.search(query)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        return self._delegate.get(item_id, version)

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        del item_id, version
        self.fetch_count += 1
        if self.fetch_count <= 2:
            return self._artifact
        return b"changed-after-revalidation"


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


class _SemanticAgentOwner:
    kind = RegistryItemType.AGENT

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"owner_domain": "agents", "item_id": item.item_id}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        return item.item_id

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        return item.item_id

    async def uninstall(self, item: RegistryItem) -> object:
        return item.item_id

    async def status(self, item: RegistryItem) -> object:
        return item.item_id

    def describe(self, item: RegistryItem) -> dict[str, object]:
        return {"owner_domain": "agents", "item_id": item.item_id}


def test_agent_dependency_plan_reuses_cross_kind_decision_engine(tmp_path: Path) -> None:
    skill, skill_artifact = _item("semantic.skill", RegistryItemType.SKILL)
    capability, capability_artifact = _item(
        "semantic.capability",
        RegistryItemType.CAPABILITY_PROVIDER,
    )
    orchestrator, orchestrator_artifact = _item(
        "semantic.orchestrator",
        RegistryItemType.ORCHESTRATOR,
    )
    model_provider, model_provider_artifact = _item(
        "semantic.model-provider",
        RegistryItemType.MODEL_PROVIDER,
    )
    agent, agent_artifact = _item(
        "semantic.agent",
        RegistryItemType.AGENT,
        dependencies=(
            RegistryDependency(skill.item_id, item_kind=RegistryItemType.SKILL),
            RegistryDependency(
                capability.item_id,
                item_kind=RegistryItemType.CAPABILITY_PROVIDER,
            ),
            RegistryDependency(
                orchestrator.item_id,
                item_kind=RegistryItemType.ORCHESTRATOR,
            ),
            RegistryDependency(
                model_provider.item_id,
                item_kind=RegistryItemType.MODEL_PROVIDER,
                optional=True,
            ),
        ),
    )
    items = (
        (skill, skill_artifact),
        (capability, capability_artifact),
        (orchestrator, orchestrator_artifact),
        (model_provider, model_provider_artifact),
        (agent, agent_artifact),
    )
    store = JsonRegistryInstallationStore(tmp_path / "semantic-dependencies.json")
    for dependency in (skill, capability, orchestrator, model_provider):
        store.record(dependency, provider_id="local")

    provider = LocalRegistryProvider(
        tuple(item for item, _artifact in items),
        {(item.item_id, item.version): artifact for item, artifact in items},
        provider_id="local",
    )
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((_SemanticAgentOwner(),)),
    )

    preview = service.preview(agent.item_id, agent.version, _context())

    assert preview.activation_allowed is True
    assert {
        (dependency.item_id, dependency.item_kind, dependency.status)
        for dependency in preview.decision.dependencies
    } == {
        (skill.item_id, "skill", DependencyStatus.SATISFIED),
        (
            capability.item_id,
            "capability_provider",
            DependencyStatus.SATISFIED,
        ),
        (
            orchestrator.item_id,
            "orchestrator",
            DependencyStatus.SATISFIED,
        ),
        (
            model_provider.item_id,
            "model_provider",
            DependencyStatus.SATISFIED,
        ),
    }


def test_marketplace_store_preserves_non_marketplace_installed_dependency(
    tmp_path: Path,
) -> None:
    persisted, persisted_artifact = _item(
        "example.marketplace-installed",
        RegistryItemType.SKILL,
    )
    root, root_artifact = _item(
        "example.local-dependent",
        RegistryItemType.WORKFLOW,
        dependencies=(
            RegistryDependency(
                "example.local-tool",
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    store = JsonRegistryInstallationStore(tmp_path / "mixed-installed.json")
    store.record(persisted, provider_id="local")
    service = _service(
        ((persisted, persisted_artifact), (root, root_artifact)),
        store=store,
    )
    local_tool = InstalledRegistryItem(
        "example.local-tool",
        "1.0.0",
        item_type=RegistryItemType.TOOL,
    )

    preview = service.preview(
        root.item_id,
        root.version,
        ValidationContext("0.0.1", installed_items=(local_tool,)),
    )

    local_resolution = next(
        resolution
        for resolution in preview.decision.dependencies
        if resolution.item_id == local_tool.item_id
    )
    assert local_resolution.status is DependencyStatus.SATISFIED
    assert local_resolution.installed_version == local_tool.version
    assert preview.activation_allowed is True


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


def test_dependency_kind_mismatch_is_explicit_and_blocks_install_plan() -> None:
    tool, tool_artifact = _item(
        "example.kind-specific-dependency",
        RegistryItemType.TOOL,
    )
    root, root_artifact = _item(
        "example.kind-specific-root",
        dependencies=(
            RegistryDependency(
                tool.item_id,
                item_kind=RegistryItemType.SKILL,
            ),
        ),
    )
    service = _service(((tool, tool_artifact), (root, root_artifact)))

    preview = service.preview(root.item_id, root.version, _context())

    dependency = next(
        resolution
        for resolution in preview.decision.dependencies
        if resolution.item_id == tool.item_id
    )
    assert dependency.status is DependencyStatus.KIND_CONFLICT
    assert preview.activation_allowed is False
    assert preview.decision.install_order == ()
    finding = next(finding for finding in preview.findings if finding.code == "dependency_kind")
    assert finding.category is FindingCategory.DEPENDENCY


def test_overlapping_transitive_constraints_choose_one_common_candidate() -> None:
    shared_v2, shared_v2_artifact = _item(
        "example.shared-common",
        version="2.0.0",
    )
    extra, extra_artifact = _item("example.shared-v3-extra")
    shared_v3, shared_v3_artifact = _item(
        shared_v2.item_id,
        version="3.0.0",
        dependencies=(RegistryDependency(extra.item_id),),
    )
    left, left_artifact = _item(
        "example.common-left",
        dependencies=(
            RegistryDependency(
                shared_v2.item_id,
                VersionRange(maximum="2.0.0"),
            ),
        ),
    )
    right, right_artifact = _item(
        "example.common-right",
        dependencies=(
            RegistryDependency(
                shared_v2.item_id,
                VersionRange(minimum="2.0.0", maximum="3.0.0"),
            ),
        ),
    )
    root, root_artifact = _item(
        "example.common-root",
        dependencies=(
            RegistryDependency(left.item_id),
            RegistryDependency(right.item_id),
        ),
    )
    service = _service(
        (
            (shared_v2, shared_v2_artifact),
            (shared_v3, shared_v3_artifact),
            (extra, extra_artifact),
            (left, left_artifact),
            (right, right_artifact),
            (root, root_artifact),
        )
    )

    preview = service.preview(root.item_id, root.version, _context())

    shared_resolutions = [
        resolution
        for resolution in preview.decision.dependencies
        if resolution.item_id == shared_v2.item_id
    ]
    assert len(shared_resolutions) == 2
    assert {resolution.candidate_version for resolution in shared_resolutions} == {"2.0.0"}
    assert not any(
        resolution.item_id == extra.item_id for resolution in preview.decision.dependencies
    )
    assert [(step.item_id, step.version) for step in preview.decision.install_order] == [
        (shared_v2.item_id, "2.0.0"),
        (right.item_id, right.version),
        (left.item_id, left.version),
        (root.item_id, root.version),
    ]


def test_dependency_environment_incompatibility_blocks_install_plan() -> None:
    dependency, dependency_artifact = _item(
        "example.environment-dependency",
        RegistryItemType.TOOL,
        supported_platform=VersionRange("2.0.0", "3.0.0"),
        compatibility=RegistryCompatibility(
            required_runtimes=frozenset({"docker"}),
        ),
    )
    root, root_artifact = _item(
        "example.environment-root",
        dependencies=(
            RegistryDependency(
                dependency.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    service = _service(
        ((dependency, dependency_artifact), (root, root_artifact)),
    )

    preview = service.preview(root.item_id, root.version, _context())

    resolution = next(
        item for item in preview.decision.dependencies if item.item_id == dependency.item_id
    )
    assert resolution.status is DependencyStatus.ENVIRONMENT_INCOMPATIBLE
    assert resolution.candidate_version == dependency.version
    assert resolution.candidate_compatibility is not None
    assert resolution.candidate_compatibility.platform_compatible is False
    assert resolution.candidate_compatibility.missing_runtimes == ("docker",)
    assert preview.decision.install_order == ()
    finding = next(
        finding
        for finding in preview.findings
        if finding.code == "dependency_environment_incompatible"
    )
    assert finding.category is FindingCategory.DEPENDENCY
    assert ("platform_compatible", "false") in finding.details
    assert ("missing_runtimes", "docker") in finding.details


def test_transitive_common_version_environment_conflict_is_explained() -> None:
    compatible_low, compatible_low_artifact = _item(
        "example.environment-intersection",
        version="1.0.0",
    )
    incompatible_common, incompatible_common_artifact = _item(
        compatible_low.item_id,
        version="2.0.0",
        supported_platform=VersionRange("2.0.0", "3.0.0"),
    )
    compatible_high, compatible_high_artifact = _item(
        compatible_low.item_id,
        version="3.0.0",
    )
    left, left_artifact = _item(
        "example.environment-intersection-left",
        dependencies=(
            RegistryDependency(
                compatible_low.item_id,
                VersionRange(minimum="1.0.0", maximum="2.0.0"),
            ),
        ),
    )
    right, right_artifact = _item(
        "example.environment-intersection-right",
        dependencies=(
            RegistryDependency(
                compatible_low.item_id,
                VersionRange(minimum="2.0.0", maximum="3.0.0"),
            ),
        ),
    )
    root, root_artifact = _item(
        "example.environment-intersection-root",
        dependencies=(
            RegistryDependency(left.item_id),
            RegistryDependency(right.item_id),
        ),
    )
    service = _service(
        (
            (compatible_low, compatible_low_artifact),
            (incompatible_common, incompatible_common_artifact),
            (compatible_high, compatible_high_artifact),
            (left, left_artifact),
            (right, right_artifact),
            (root, root_artifact),
        )
    )

    preview = service.preview(root.item_id, root.version, _context())

    aggregate = next(
        resolution
        for resolution in preview.decision.dependencies
        if resolution.item_id == compatible_low.item_id and resolution.required_by == root.item_id
    )
    assert aggregate.status is DependencyStatus.ENVIRONMENT_INCOMPATIBLE
    assert aggregate.candidate_version == incompatible_common.version
    assert aggregate.candidate_compatibility is not None
    assert aggregate.candidate_compatibility.platform_compatible is False
    assert preview.decision.install_order == ()
    assert any(
        finding.code == "dependency_environment_incompatible"
        and finding.subject == compatible_low.item_id
        for finding in preview.findings
    )


def test_environment_selection_does_not_hide_cross_source_ambiguity() -> None:
    source_a_dependency, source_a_artifact = _item(
        "example.environment-source-shared",
        RegistryItemType.TOOL,
    )
    source_b_dependency, source_b_artifact = _item(
        source_a_dependency.item_id,
        RegistryItemType.TOOL,
        supported_platform=VersionRange("2.0.0", "3.0.0"),
    )
    left, left_artifact = _item(
        "example.environment-source-left",
        dependencies=(RegistryDependency(source_a_dependency.item_id),),
    )
    right, right_artifact = _item(
        "example.environment-source-right",
        dependencies=(RegistryDependency(source_a_dependency.item_id),),
    )
    root, root_artifact = _item(
        "example.environment-source-root",
        dependencies=(
            RegistryDependency(left.item_id),
            RegistryDependency(right.item_id),
        ),
    )
    root_provider = LocalRegistryProvider(
        (left, right, root),
        {
            (left.item_id, left.version): left_artifact,
            (right.item_id, right.version): right_artifact,
            (root.item_id, root.version): root_artifact,
        },
        provider_id="root-source",
    )
    source_a = LocalRegistryProvider(
        (source_a_dependency,),
        {(source_a_dependency.item_id, source_a_dependency.version): source_a_artifact},
        provider_id="source-a",
    )
    source_b = LocalRegistryProvider(
        (source_b_dependency,),
        {(source_b_dependency.item_id, source_b_dependency.version): source_b_artifact},
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

    shared = [
        resolution
        for resolution in preview.decision.dependencies
        if resolution.item_id == source_a_dependency.item_id
    ]
    assert shared
    assert all(resolution.status is DependencyStatus.SOURCE_AMBIGUOUS for resolution in shared)
    assert preview.decision.install_order == ()


def test_dependency_resolution_prefers_latest_environment_compatible_candidate() -> None:
    compatible, compatible_artifact = _item(
        "example.environment-versioned",
        RegistryItemType.TOOL,
        version="1.0.0",
    )
    incompatible, incompatible_artifact = _item(
        compatible.item_id,
        RegistryItemType.TOOL,
        version="2.0.0",
        supported_platform=VersionRange("2.0.0", "3.0.0"),
    )
    root, root_artifact = _item(
        "example.environment-version-root",
        dependencies=(
            RegistryDependency(
                compatible.item_id,
                VersionRange(maximum="2.0.0"),
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    service = _service(
        (
            (compatible, compatible_artifact),
            (incompatible, incompatible_artifact),
            (root, root_artifact),
        )
    )

    preview = service.preview(root.item_id, root.version, _context())

    resolution = next(
        item for item in preview.decision.dependencies if item.item_id == compatible.item_id
    )
    assert resolution.status is DependencyStatus.AVAILABLE
    assert resolution.candidate_version == compatible.version
    assert resolution.candidate_compatibility is not None
    assert resolution.candidate_compatibility.compatible is True
    assert [(step.item_id, step.version) for step in preview.decision.install_order] == [
        (compatible.item_id, compatible.version),
        (root.item_id, root.version),
    ]


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


def test_capability_plugin_connector_and_model_requirements_are_typed() -> None:
    base, artifact = _item("example.owner-requirements")
    item = replace(
        base,
        required_capabilities=frozenset({"capability.example"}),
        required_plugins=("plugin.example",),
        required_connectors=("connector.example",),
        required_models=("model.example",),
    )
    service = _service(((item, artifact),))

    preview = service.preview(item.item_id, item.version, ValidationContext("0.0.1"))

    compatibility = preview.decision.compatibility
    assert compatibility.compatible is False
    assert compatibility.missing_capabilities == ("capability.example",)
    assert compatibility.missing_plugins == ("plugin.example",)
    assert compatibility.missing_connectors == ("connector.example",)
    assert compatibility.missing_models == ("model.example",)
    assert preview.activation_allowed is False
    codes = {finding.code for finding in preview.findings}
    assert {
        "missing_capability",
        "missing_plugin",
        "missing_connector",
        "missing_model",
    } <= codes


def test_update_dependency_diff_is_typed_and_deterministic(tmp_path: Path) -> None:
    unchanged = RegistryDependency(
        "example.dep-unchanged",
        item_kind=RegistryItemType.TOOL,
    )
    removed = RegistryDependency(
        "example.dep-removed",
        VersionRange(maximum="1.0.0"),
        item_kind=RegistryItemType.SKILL,
    )
    changed_old = RegistryDependency(
        "example.dep-changed",
        VersionRange(maximum="1.0.0"),
        item_kind=RegistryItemType.PLUGIN,
    )
    changed_new = RegistryDependency(
        changed_old.item_id,
        VersionRange(minimum="2.0.0"),
        optional=True,
        item_kind=RegistryItemType.PLUGIN,
    )
    added = RegistryDependency(
        "example.dep-added",
        optional=True,
        item_kind=RegistryItemType.CONNECTOR,
    )
    old, old_artifact = _item(
        "example.dependency-diff",
        version="1.0.0",
        dependencies=(removed, unchanged, changed_old),
    )
    candidate, candidate_artifact = _item(
        old.item_id,
        version="2.0.0",
        dependencies=(unchanged, added, changed_new),
    )
    store = JsonRegistryInstallationStore(tmp_path / "dependency-diff.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    diff = preview.decision.dependency_diff
    assert diff.installed is True
    assert diff.previous_known is True
    assert [dependency.item_id for dependency in diff.previous] == [
        changed_old.item_id,
        removed.item_id,
        unchanged.item_id,
    ]
    assert [dependency.item_id for dependency in diff.requested] == [
        added.item_id,
        changed_new.item_id,
        unchanged.item_id,
    ]
    assert [dependency.item_id for dependency in diff.added] == [added.item_id]
    assert [dependency.item_id for dependency in diff.removed] == [removed.item_id]
    assert [dependency.item_id for dependency in diff.unchanged] == [unchanged.item_id]
    assert len(diff.changes) == 1
    assert diff.changes[0].previous == changed_old
    assert diff.changes[0].requested == changed_new
    assert diff.changed is True
    finding = next(finding for finding in preview.findings if finding.code == "dependency_changed")
    assert finding.category is FindingCategory.DEPENDENCY
    assert ("added", added.item_id) in finding.details
    assert ("removed", removed.item_id) in finding.details
    assert ("changed", changed_old.item_id) in finding.details


def test_update_dependency_diff_preserves_unknown_legacy_previous_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-dependency-diff.json"
    path.write_text(
        json.dumps(
            {
                "version": "1",
                "installations": [
                    {
                        "current": {
                            "item_id": "example.legacy-dependency-diff",
                            "version": "1.0.0",
                            "source_registry": "local",
                            "source_repository": "https://example.invalid/legacy-dependency-diff",
                            "package_reference": "example.legacy-dependency-diff@1.0.0",
                            "revision": "legacy-rev",
                            "license": "MIT",
                            "provenance": "legacy-source",
                        },
                        "pinned_version": None,
                        "history": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    candidate, candidate_artifact = _item(
        "example.legacy-dependency-diff",
        version="2.0.0",
        dependencies=(RegistryDependency("example.new-dependency", optional=True),),
    )
    store = JsonRegistryInstallationStore(path)
    service = _service(((candidate, candidate_artifact),), store=store)

    preview = service.preview(candidate.item_id, candidate.version, _context())

    diff = preview.decision.dependency_diff
    assert diff.installed is True
    assert diff.previous_known is False
    assert diff.previous == ()
    assert diff.requested == candidate.dependencies
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.changes == ()
    assert diff.unchanged == ()
    assert diff.changed is False
    assert not any(finding.code == "dependency_changed" for finding in preview.findings)


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


def test_license_and_provenance_changes_are_typed(tmp_path: Path) -> None:
    old, old_artifact = _item("example.provenance-metadata", version="1.0.0")
    candidate_base, candidate_artifact = _item(
        old.item_id,
        version="1.1.0",
    )
    candidate = replace(
        candidate_base,
        license="Apache-2.0",
        provenance="reviewed-community-release",
    )
    store = JsonRegistryInstallationStore(tmp_path / "provenance-metadata.json")
    store.record(old, provider_id="local")
    service = _service(
        ((old, old_artifact), (candidate, candidate_artifact)),
        store=store,
    )

    preview = service.preview(candidate.item_id, candidate.version, _context())

    diff = preview.decision.provenance_diff
    assert diff.previous_license == old.license
    assert diff.candidate_license == candidate.license
    assert diff.license_changed is True
    assert diff.previous_provenance == old.provenance
    assert diff.candidate_provenance == candidate.provenance
    assert diff.provenance_changed is True
    assert diff.changed is True
    codes = {finding.code for finding in preview.findings}
    assert {"license_changed", "provenance_changed"} <= codes


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


def test_safe_transitive_dependencies_publish_leaf_first_install_order() -> None:
    leaf, leaf_artifact = _item(
        "example.install-leaf",
        RegistryItemType.TOOL,
    )
    middle, middle_artifact = _item(
        "example.install-middle",
        RegistryItemType.SKILL,
        dependencies=(
            RegistryDependency(
                leaf.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    root, root_artifact = _item(
        "example.install-root",
        RegistryItemType.WORKFLOW,
        dependencies=(
            RegistryDependency(
                middle.item_id,
                item_kind=RegistryItemType.SKILL,
            ),
        ),
    )
    service = _service(
        (
            (leaf, leaf_artifact),
            (middle, middle_artifact),
            (root, root_artifact),
        )
    )

    preview = service.preview(root.item_id, root.version, _context())

    assert preview.activation_allowed is False
    assert [
        (step.item_id, step.item_kind, step.version, step.source_registry)
        for step in preview.decision.install_order
    ] == [
        ("example.install-leaf", "tool", "1.0.0", "local"),
        ("example.install-middle", "skill", "1.0.0", "local"),
        ("example.install-root", "workflow", "1.0.0", "local"),
    ]


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


def test_uninstall_preview_uses_persisted_dependency_evidence_after_catalog_drift(
    tmp_path: Path,
) -> None:
    tool, tool_artifact = _item("example.persisted-uninstall-tool", RegistryItemType.TOOL)
    installed_workflow, workflow_artifact = _item(
        "example.persisted-uninstall-workflow",
        RegistryItemType.WORKFLOW,
        dependencies=(
            RegistryDependency(
                tool.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    drifted_workflow = replace(installed_workflow, dependencies=())
    state_path = tmp_path / "installations.json"
    store = JsonRegistryInstallationStore(state_path)
    store.record(tool, provider_id="local")
    store.record(installed_workflow, provider_id="local")
    store = JsonRegistryInstallationStore(state_path)
    service = _service(
        ((tool, tool_artifact), (drifted_workflow, workflow_artifact)),
        store=store,
    )

    preview = service.preview_uninstall(tool.item_id)

    assert preview.activation_allowed is False
    assert preview.decision.dependencies[0].required_by == installed_workflow.item_id
    assert preview.decision.dependencies[0].status is DependencyStatus.REQUIRED_BY_INSTALLED


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
        version="2.0.0",
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


def test_single_registry_provider_cannot_spoof_source_identity() -> None:
    item, artifact = _item("example.source-spoof")
    spoofed = replace(item, source_registry="other-registry")
    provider = LocalRegistryProvider(
        (spoofed,),
        {(spoofed.item_id, spoofed.version): artifact},
        provider_id="local",
    )
    service = DistributionService(provider, _Router())

    with pytest.raises(ValueError, match="conflicting source_registry"):
        service.get(spoofed.item_id, spoofed.version)


def test_persisted_installed_dependency_detects_cycle_after_catalog_drift(
    tmp_path: Path,
) -> None:
    root_id = "example.persisted-cycle-root"
    installed, _installed_artifact = _item(
        "example.persisted-cycle-tool",
        RegistryItemType.TOOL,
        dependencies=(RegistryDependency(root_id),),
    )
    root, root_artifact = _item(
        root_id,
        dependencies=(
            RegistryDependency(
                installed.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    state_path = tmp_path / "installations.json"
    store = JsonRegistryInstallationStore(state_path)
    store.record(installed, provider_id="local")
    store = JsonRegistryInstallationStore(state_path)
    service = _service(((root, root_artifact),), store=store)

    preview = service.preview(root.item_id, root.version, _context())

    assert preview.activation_allowed is False
    assert preview.decision.install_order == ()
    assert any(
        dependency.status is DependencyStatus.CYCLE
        and dependency.path == (root.item_id, installed.item_id, root.item_id)
        for dependency in preview.decision.dependencies
    )
    assert any(finding.code == "dependency_cycle" for finding in preview.findings)


def test_installed_dependency_evidence_wins_over_same_version_catalog_drift(
    tmp_path: Path,
) -> None:
    installed, installed_artifact = _item(
        "example.same-version-installed",
        RegistryItemType.TOOL,
    )
    drifted = replace(
        installed,
        dependencies=(RegistryDependency("example.catalog-only-transitive"),),
    )
    root, root_artifact = _item(
        "example.same-version-root",
        dependencies=(
            RegistryDependency(
                installed.item_id,
                item_kind=RegistryItemType.TOOL,
            ),
        ),
    )
    state_path = tmp_path / "installations.json"
    store = JsonRegistryInstallationStore(state_path)
    store.record(installed, provider_id="local")
    store = JsonRegistryInstallationStore(state_path)
    service = _service(
        ((drifted, installed_artifact), (root, root_artifact)),
        store=store,
    )

    preview = service.preview(root.item_id, root.version, _context())

    assert preview.activation_allowed is True
    assert preview.decision.dependencies[0].status is DependencyStatus.SATISFIED
    assert all(
        dependency.item_id != "example.catalog-only-transitive"
        for dependency in preview.decision.dependencies
    )


def test_activation_rechecks_final_handoff_artifact_digest() -> None:
    item, artifact = _item("example.handoff-integrity")
    provider = _ChangingArtifactProvider(item, artifact)
    router = _RecordingRouter()
    service = DistributionService(provider, router)
    context = _context()

    preview = service.preview(item.item_id, item.version, context)

    assert preview.activation_allowed is True
    with pytest.raises(RuntimeError, match="changed immediately before activation"):
        asyncio.run(service.activate(preview, context, authorized=True))

    assert provider.fetch_count == 3
    assert router.calls == []
