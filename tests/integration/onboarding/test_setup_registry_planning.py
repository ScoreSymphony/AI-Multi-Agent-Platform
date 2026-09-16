from __future__ import annotations

import asyncio
from typing import cast

from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.onboarding import (
    JsonSetupSessionStore,
    OnboardingComponentSetupService,
    OnboardingService,
)
from ai_multi_agent_platform.onboarding.components import (
    CompatibilityEnvironment,
    SetupMode,
    SetupProfile,
)
from ai_multi_agent_platform.onboarding.setup_lifecycle import (
    ProvisioningActionKind,
    ProvisioningActionState,
    ProvisioningOutcome,
    RegistrySelection,
    SetupSessionRecord,
)
from ai_multi_agent_platform.onboarding.setup_registry_contracts import (
    SetupRegistryDependency,
    SetupRegistryItem,
)
from ai_multi_agent_platform.onboarding.setup_registry_planning import (
    DependencyAwareBrowserFirstSetupService,
)


class _Registry:
    enabled = True
    mutation_enabled = True

    def __init__(
        self,
        items: tuple[SetupRegistryItem, ...],
        *,
        installed_versions: dict[str, str] | None = None,
    ) -> None:
        self.items = items
        self.installed_versions = dict(installed_versions or {})
        self.activations: list[str] = []

    def search(self) -> tuple[SetupRegistryItem, ...]:
        return self.items

    def get(self, item_id: str, version: str) -> SetupRegistryItem:
        for item in self.items:
            if item.item_id == item_id and item.version == version:
                return item
        raise LookupError(f"registry item {item_id!r}@{version!r} not found")

    def installed_version(self, item_id: str) -> str | None:
        return self.installed_versions.get(item_id)

    async def preview(self, context, item_id: str, version: str) -> None:
        del context
        self.get(item_id, version)

    async def activate(self, context, item_id: str, version: str) -> None:
        del context
        self.get(item_id, version)
        self.activations.append(item_id)
        self.installed_versions[item_id] = version


class _Components:
    def discovered_components(self):
        return ()

    def active_profile(self):
        return None


class _ReadyComponents(_Components):
    def active_profile(self):
        return SetupProfile(profile_id="ready-profile", mode=SetupMode.AUTO, defaults={})

    def environment(self):
        return CompatibilityEnvironment()


class _Onboarding:
    def status(self, context):
        del context
        return {
            "state": "needs_model",
            "local_model_count": 0,
            "self_hosted_model_count": 0,
        }

    async def status_async(self, context):
        return self.status(context)


class _ReadyOnboarding(_Onboarding):
    def status(self, context):
        del context
        return {
            "state": "ready_for_task",
            "local_model_count": 1,
            "self_hosted_model_count": 0,
        }


def _item(
    item_id: str,
    version: str,
    *,
    dependencies: tuple[SetupRegistryDependency, ...] = (),
) -> SetupRegistryItem:
    return SetupRegistryItem(
        item_id=item_id,
        name=item_id.title(),
        description=f"{item_id} test item",
        version=version,
        categories=(),
        route="plugin",
        deprecated=False,
        yanked=False,
        dependencies=dependencies,
        license="MIT",
        source_repository="https://example.invalid/repository",
    )


def _service(
    tmp_path,
    registry: _Registry,
) -> DependencyAwareBrowserFirstSetupService:
    return DependencyAwareBrowserFirstSetupService(
        cast(OnboardingComponentSetupService, _Components()),
        cast(OnboardingService, _Onboarding()),
        JsonSetupSessionStore(tmp_path / "setup-sessions.json"),
        registry=registry,
    )


def _session(*selections: RegistrySelection) -> SetupSessionRecord:
    return SetupSessionRecord(principal_ref="user-1", registry_items=selections)


def _context(key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        idempotency_key=key,
        actor=ActorContext(
            principal_ref="user-1",
            owner_type="user",
            owner_id="user-1",
            actor_type="human",
        ),
    )


def test_transitive_dependencies_are_planned_before_parent(tmp_path) -> None:
    leaf = _item("leaf", "1.0")
    middle = _item(
        "middle",
        "1.0",
        dependencies=(SetupRegistryDependency("leaf", minimum_version="1.0"),),
    )
    root = _item(
        "root",
        "1.0",
        dependencies=(SetupRegistryDependency("middle", minimum_version="1.0"),),
    )
    service = _service(tmp_path, _Registry((root, middle, leaf)))

    plan = service._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )

    assert [action.component_ref for action in plan] == ["leaf@1.0", "middle@1.0", "root@1.0"]
    assert plan[1].dependencies == ("leaf@1.0",)
    assert plan[2].dependencies == ("middle@1.0",)
    assert {action.state for action in plan} == {ProvisioningActionState.PENDING}


def test_targeted_parent_retry_expands_required_dependencies_in_plan_order(tmp_path) -> None:
    leaf = _item("leaf", "1.0")
    middle = _item(
        "middle",
        "1.0",
        dependencies=(SetupRegistryDependency("leaf"),),
    )
    root = _item(
        "root",
        "1.0",
        dependencies=(SetupRegistryDependency("middle"),),
    )
    registry = _Registry((root, middle, leaf))
    service = _service(tmp_path, registry)
    service._sessions["user-1"] = _session(RegistrySelection(item_id="root", version="1.0"))

    result = asyncio.run(
        service.provision(
            _context("targeted-root"),
            "initial-setup",
            {"action_ids": ["registry:root@1.0"]},
        )
    )

    assert registry.activations == ["leaf", "middle", "root"]
    assert result["outcome"]["state"] == "completed"


def test_explicit_dependency_version_conflict_blocks_parent_before_mutation(tmp_path) -> None:
    dependency = _item("dependency", "1.0")
    root = _item(
        "root",
        "1.0",
        dependencies=(SetupRegistryDependency("dependency", minimum_version="2.0"),),
    )
    service = _service(tmp_path, _Registry((root, dependency)))

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
        dependencies=(SetupRegistryDependency("second"),),
    )
    second = _item(
        "second",
        "1.0",
        dependencies=(SetupRegistryDependency("first"),),
    )
    service = _service(tmp_path, _Registry((first, second)))

    plan = service._registry_dependency_plan(
        _session(RegistrySelection(item_id="first", version="1.0"))
    )

    assert {action.state for action in plan} == {ProvisioningActionState.BLOCKED}
    assert any(
        "registry dependency cycle detected" in blocker
        for action in plan
        for blocker in action.blockers
    )


def test_exact_version_reuse_and_mismatch_requires_mutation(tmp_path) -> None:
    root = _item("root", "1.0")
    exact = _service(tmp_path / "exact", _Registry((root,), installed_versions={"root": "1.0"}))
    different = _service(
        tmp_path / "different",
        _Registry((root,), installed_versions={"root": "0.9"}),
    )

    exact_action = exact._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )[0]
    different_action = different._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )[0]
    exact_card = exact._registry_card(root)
    different_card = different._registry_card(root)

    assert exact_action.kind is ProvisioningActionKind.REUSE
    assert exact_action.state is ProvisioningActionState.COMPLETED
    assert different_action.state is ProvisioningActionState.PENDING
    assert exact_card["install_status"] == "installed"
    assert different_card["install_status"] == "installable"


def test_live_registry_state_overrides_stale_setup_outcomes(tmp_path) -> None:
    root = _item("root", "1.0")
    selection = RegistrySelection(item_id="root", version="1.0")
    stale_completed = ProvisioningOutcome(
        action_id="registry:root@1.0",
        state=ProvisioningActionState.COMPLETED,
        idempotency_key="old-completed",
        updated_at="2026-09-16T00:00:00+00:00",
    )
    stale_failed = ProvisioningOutcome(
        action_id="registry:root@1.0",
        state=ProvisioningActionState.FAILED,
        idempotency_key="old-failed",
        updated_at="2026-09-16T00:00:00+00:00",
        error_code="backend_error",
        error_message="owner committed before response failed",
    )

    missing = _service(tmp_path / "missing", _Registry((root,)))
    installed = _service(
        tmp_path / "installed",
        _Registry((root,), installed_versions={"root": "1.0"}),
    )

    missing_action = missing._registry_dependency_plan(
        SetupSessionRecord(
            principal_ref="user-1",
            registry_items=(selection,),
            outcomes=(stale_completed,),
        )
    )[0]
    installed_action = installed._registry_dependency_plan(
        SetupSessionRecord(
            principal_ref="user-1",
            registry_items=(selection,),
            outcomes=(stale_failed,),
        )
    )[0]

    assert missing_action.kind is ProvisioningActionKind.INSTALL
    assert missing_action.state is ProvisioningActionState.PENDING
    assert installed_action.kind is ProvisioningActionKind.REUSE
    assert installed_action.state is ProvisioningActionState.COMPLETED


def test_pending_registry_mutation_blocks_dashboard_readiness(tmp_path) -> None:
    root = _item("root", "1.0")
    service = DependencyAwareBrowserFirstSetupService(
        cast(OnboardingComponentSetupService, _ReadyComponents()),
        cast(OnboardingService, _ReadyOnboarding()),
        JsonSetupSessionStore(tmp_path / "setup-sessions.json"),
        registry=_Registry((root,)),
    )
    service._sessions["user-1"] = _session(RegistrySelection(item_id="root", version="1.0"))

    status = service.status(_context("readiness"))

    assert status["current_step"] == "validation"
    assert status["readiness"]["ready"] is False
    assert status["readiness"]["dashboard_allowed"] is False
    assert "registry:root@1.0" in status["readiness"]["blocking_actions"]
    ready_step = next(step for step in status["steps"] if step["id"] == "ready")
    assert ready_step["state"] == "blocked"


def test_optional_registry_dependency_does_not_block_or_expand_plan(tmp_path) -> None:
    root = _item(
        "root",
        "1.0",
        dependencies=(SetupRegistryDependency("optional", optional=True),),
    )
    service = _service(tmp_path, _Registry((root,)))

    plan = service._registry_dependency_plan(
        _session(RegistrySelection(item_id="root", version="1.0"))
    )

    assert [action.component_ref for action in plan] == ["root@1.0"]
    assert plan[0].dependencies == ()
    assert plan[0].state is ProvisioningActionState.PENDING
