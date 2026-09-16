from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.distribution import DistributionService
from ai_multi_agent_platform.distribution.control_plane import RegistryCommandHandlers
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import (
    RegistryDependency,
    RegistryItemType,
    RegistrySource,
    VersionRange,
)
from ai_multi_agent_platform.distribution.state import (
    RegistryInstallation,
    RegistryInstallationSnapshot,
)
from ai_multi_agent_platform.onboarding import (
    JsonSetupSessionStore,
    OnboardingComponentSetupService,
    OnboardingService,
)
from ai_multi_agent_platform.onboarding.setup_lifecycle import (
    ProvisioningActionState,
    RegistrySelection,
    SetupSessionRecord,
)
from ai_multi_agent_platform.onboarding.setup_registry_planning import (
    DependencyAwareBrowserFirstSetupService,
)


class _Distribution:
    def __init__(
        self,
        items: tuple[RegistryItem, ...],
        *,
        installed_versions: dict[str, str] | None = None,
    ) -> None:
        self.items = items
        self.installed_versions = installed_versions or {}

    def search(self) -> tuple[RegistryItem, ...]:
        return self.items

    def get(self, item_id: str, version: str) -> RegistryItem:
        for item in self.items:
            if item.item_id == item_id and item.version == version:
                return item
        raise LookupError(f"registry item {item_id!r}@{version!r} not found")

    def installed(self, item_id: str) -> RegistryInstallation | None:
        version = self.installed_versions.get(item_id)
        if version is None:
            return None
        return RegistryInstallation(
            RegistryInstallationSnapshot(
                item_id=item_id,
                version=version,
                source_registry="test-registry",
                source_repository="https://example.invalid/repository",
                package_reference=f"pkg:{item_id}@{version}",
                revision=None,
                license="MIT",
                provenance="test",
                item_type=RegistryItemType.PLUGIN,
            )
        )


def _item(
    item_id: str,
    version: str,
    *,
    dependencies: tuple[RegistryDependency, ...] = (),
) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=RegistryItemType.PLUGIN,
        name=item_id.title(),
        description=f"{item_id} test item",
        version=version,
        publisher="tests",
        source=RegistrySource(
            repository="https://example.invalid/repository",
            package_reference=f"pkg:{item_id}@{version}",
        ),
        license="MIT",
        provenance="test",
        dependencies=dependencies,
    )


def _service(tmp_path, distribution: _Distribution) -> DependencyAwareBrowserFirstSetupService:
    return DependencyAwareBrowserFirstSetupService(
        cast(OnboardingComponentSetupService, object()),
        cast(OnboardingService, object()),
        JsonSetupSessionStore(tmp_path / "setup-sessions.json"),
        distribution=cast(DistributionService, distribution),
        registry_commands=cast(RegistryCommandHandlers, object()),
    )


def _session(*selections: RegistrySelection) -> SetupSessionRecord:
    return SetupSessionRecord(principal_ref="user-1", registry_items=selections)


def test_transitive_dependencies_are_planned_before_parent(tmp_path) -> None:
    leaf = _item("leaf", "1.0")
    middle = _item(
        "middle",
        "1.0",
        dependencies=(RegistryDependency("leaf", VersionRange(minimum="1.0")),),
    )
    root = _item(
        "root",
        "1.0",
        dependencies=(RegistryDependency("middle", VersionRange(minimum="1.0")),),
    )
    service = _service(tmp_path, _Distribution((root, middle, leaf)))

    plan = service._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )

    assert [action.component_ref for action in plan] == ["leaf@1.0", "middle@1.0", "root@1.0"]
    assert plan[1].dependencies == ("leaf@1.0",)
    assert plan[2].dependencies == ("middle@1.0",)
    assert {action.state for action in plan} == {ProvisioningActionState.PENDING}


def test_explicit_dependency_version_conflict_blocks_parent_before_mutation(tmp_path) -> None:
    dependency = _item("dependency", "1.0")
    root = _item(
        "root",
        "1.0",
        dependencies=(RegistryDependency("dependency", VersionRange(minimum="2.0")),),
    )
    service = _service(tmp_path, _Distribution((root, dependency)))

    plan = service._registry_dependency_plan(
        _session(
            RegistrySelection(item_id="root", version="1.0"),
            RegistrySelection(item_id="dependency", version="1.0"),
        )
    )

    root_action = next(action for action in plan if action.component_ref == "root@1.0")
    assert root_action.state is ProvisioningActionState.BLOCKED
    assert any("violates the required range" in blocker for blocker in root_action.blockers)


def test_dependency_cycle_blocks_all_members_of_cycle(tmp_path) -> None:
    first = _item(
        "first",
        "1.0",
        dependencies=(RegistryDependency("second"),),
    )
    second = _item(
        "second",
        "1.0",
        dependencies=(RegistryDependency("first"),),
    )
    service = _service(tmp_path, _Distribution((first, second)))

    plan = service._registry_dependency_plan(
        _session(RegistrySelection(item_id="first", version="1.0"))
    )

    assert {action.state for action in plan} == {ProvisioningActionState.BLOCKED}
    assert any(
        "registry dependency cycle detected" in blocker
        for action in plan
        for blocker in action.blockers
    )


def test_exact_installed_version_is_reused_but_different_version_requires_mutation(tmp_path) -> None:
    root = _item("root", "1.0")
    exact = _service(tmp_path / "exact", _Distribution((root,), installed_versions={"root": "1.0"}))
    different = _service(
        tmp_path / "different",
        _Distribution((root,), installed_versions={"root": "0.9"}),
    )

    exact_action = exact._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )[0]
    different_action = different._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )[0]

    assert exact_action.state is ProvisioningActionState.COMPLETED
    assert different_action.state is ProvisioningActionState.PENDING


def test_optional_registry_dependency_does_not_block_or_expand_plan(tmp_path) -> None:
    root = _item(
        "root",
        "1.0",
        dependencies=(RegistryDependency("optional", optional=True),),
    )
    service = _service(tmp_path, _Distribution((root,)))

    plan = service._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )

    assert [action.component_ref for action in plan] == ["root@1.0"]
    assert plan[0].dependencies == ()
    assert plan[0].state is ProvisioningActionState.PENDING
