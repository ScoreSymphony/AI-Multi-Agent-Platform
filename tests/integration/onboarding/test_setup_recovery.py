from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.distribution import DistributionService
from ai_multi_agent_platform.distribution.control_plane import RegistryCommandHandlers
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import RegistryItemType, RegistrySource
from ai_multi_agent_platform.distribution.state import (
    RegistryInstallation,
    RegistryInstallationSnapshot,
)
from ai_multi_agent_platform.onboarding import (
    CompatibilityEnvironment,
    ComponentAvailability,
    ComponentCategory,
    ComponentLifecycle,
    DiscoveredComponent,
    JsonSetupProfileStore,
    JsonSetupSessionStore,
    OnboardingComponentSetupService,
    OnboardingService,
    SetupMode,
)
from ai_multi_agent_platform.onboarding.setup_registry_planning import (
    DependencyAwareBrowserFirstSetupService,
)


class _Discovery:
    def discover(self) -> tuple[DiscoveredComponent, ...]:
        return (
            DiscoveredComponent(
                component_id="reference-orchestrator",
                category=ComponentCategory.ORCHESTRATOR,
                display_name="Reference Orchestrator",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                recommended_modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL}),
                source_ref="platform:orchestration/reference",
            ),
        )

    def environment(self) -> CompatibilityEnvironment:
        return CompatibilityEnvironment()


class _OnboardingStatus:
    def status(self, context: RequestContext):
        del context
        return {
            "id": "first-run",
            "type": "onboarding_status",
            "state": "needs_project",
            "local_model_count": 1,
            "self_hosted_model_count": 0,
        }


class _Distribution:
    enabled = True

    def __init__(self, items: tuple[RegistryItem, ...]) -> None:
        self.items = items
        self.installations: dict[str, RegistryInstallation] = {}

    def search(self) -> tuple[RegistryItem, ...]:
        return self.items

    def get(self, item_id: str, version: str) -> RegistryItem:
        for item in self.items:
            if item.item_id == item_id and item.version == version:
                return item
        raise LookupError(f"registry item {item_id!r}@{version!r} not found")

    def installed(self, item_id: str) -> RegistryInstallation | None:
        return self.installations.get(item_id)

    def install(self, item_id: str, version: str) -> None:
        item = self.get(item_id, version)
        self.installations[item_id] = RegistryInstallation(
            RegistryInstallationSnapshot(
                item_id=item.item_id,
                version=item.version,
                source_registry="test-registry",
                source_repository=item.source.repository,
                package_reference=item.source.package_reference,
                revision=item.source.revision,
                license=item.license,
                provenance=item.provenance,
                item_type=item.item_type,
            )
        )


class _RegistryCommands:
    def __init__(self, distribution: _Distribution, *, fail_once: set[str] | None = None) -> None:
        self.distribution = distribution
        self.fail_once = set(fail_once or ())
        self.activations: list[str] = []

    async def preview(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        del context
        version = payload.get("version")
        assert isinstance(version, str)
        self.distribution.get(resource_ref, version)
        return {"status": "ready"}

    async def activate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        del context
        version = payload.get("version")
        assert isinstance(version, str)
        ref = f"{resource_ref}@{version}"
        self.activations.append(ref)
        if ref in self.fail_once:
            self.fail_once.remove(ref)
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "simulated owner-domain activation failure",
                retryable=True,
            )
        self.distribution.install(resource_ref, version)
        return {"status": "applied"}


def _item(item_id: str) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=RegistryItemType.PLUGIN,
        name=item_id.title(),
        description=f"{item_id} recovery fixture",
        version="1.0",
        publisher="tests",
        source=RegistrySource(
            repository="https://example.invalid/repository",
            package_reference=f"pkg:{item_id}@1.0",
        ),
        license="MIT",
        provenance="test",
    )


def _context(idempotency_key: str | None = None) -> RequestContext:
    return RequestContext(
        request_id="request-1",
        correlation_id="correlation-1",
        actor=ActorContext(
            principal_ref="user-1",
            owner_type="user",
            owner_id="user-1",
            actor_type="human",
        ),
        idempotency_key=idempotency_key,
    )


def _component_setup(root: Path) -> OnboardingComponentSetupService:
    service = OnboardingComponentSetupService(
        _Discovery(),
        JsonSetupProfileStore(root / "component-profiles.json"),
    )
    if service.active_profile() is None:
        asyncio.run(
            service.save_profile(
                _context(),
                "component-setup",
                {"profile_id": "browser-first", "mode": "auto", "activate": True},
            )
        )
    return service


def _service(
    root: Path,
    distribution: _Distribution,
    commands: _RegistryCommands,
) -> DependencyAwareBrowserFirstSetupService:
    return DependencyAwareBrowserFirstSetupService(
        _component_setup(root),
        cast(OnboardingService, _OnboardingStatus()),
        JsonSetupSessionStore(root / "setup-sessions.json"),
        distribution=cast(DistributionService, distribution),
        registry_commands=cast(RegistryCommandHandlers, commands),
    )


def _select(lifecycle: DependencyAwareBrowserFirstSetupService, *item_ids: str) -> None:
    asyncio.run(
        lifecycle.update_session(
            _context(),
            "initial-setup",
            {
                "current_step": "components",
                "registry_items": [
                    {"item_id": item_id, "version": "1.0"} for item_id in item_ids
                ],
            },
        )
    )


def test_partial_failure_persists_across_restart_replays_final_failure_and_retries(tmp_path) -> None:
    distribution = _Distribution((_item("alpha"), _item("beta")))
    commands = _RegistryCommands(distribution, fail_once={"beta@1.0"})
    lifecycle = _service(tmp_path, distribution, commands)
    _select(lifecycle, "alpha", "beta")

    failed = asyncio.run(
        lifecycle.provision(_context("operation-1"), "initial-setup", {})
    )

    assert failed["outcome"]["action_id"] == "registry:beta@1.0"
    assert failed["outcome"]["state"] == "failed"
    assert commands.activations == ["alpha@1.0", "beta@1.0"]

    restarted = _service(tmp_path, distribution, commands)
    restarted_status = restarted.status(_context())
    by_ref = {
        action["component_ref"]: action
        for action in restarted_status["plan"]["actions"]
        if action["owner"] == "registry"
    }
    assert by_ref["alpha@1.0"]["state"] == "completed"
    assert by_ref["beta@1.0"]["state"] == "failed"
    assert restarted_status["readiness"]["ready"] is False

    replayed = asyncio.run(
        restarted.provision(_context("operation-1"), "initial-setup", {})
    )
    assert replayed["replayed"] is True
    assert replayed["outcome"]["action_id"] == "registry:beta@1.0"
    assert replayed["outcome"]["state"] == "failed"
    assert commands.activations == ["alpha@1.0", "beta@1.0"]

    retried = asyncio.run(
        restarted.provision(_context("operation-2"), "initial-setup", {})
    )
    assert retried["replayed"] is False
    assert retried["outcome"]["action_id"] == "registry:beta@1.0"
    assert retried["outcome"]["state"] == "completed"
    assert commands.activations == ["alpha@1.0", "beta@1.0", "beta@1.0"]


def test_restart_reconciles_owner_installation_even_if_setup_outcome_was_not_saved(tmp_path) -> None:
    distribution = _Distribution((_item("gamma"),))
    commands = _RegistryCommands(distribution)
    lifecycle = _service(tmp_path, distribution, commands)
    _select(lifecycle, "gamma")

    # Simulate a crash after the canonical owner domain committed installation but before the setup
    # coordinator could persist its redacted outcome.
    distribution.install("gamma", "1.0")

    restarted = _service(tmp_path, distribution, commands)
    status = restarted.status(_context())
    action = next(
        item for item in status["plan"]["actions"] if item["component_ref"] == "gamma@1.0"
    )
    assert action["state"] == "completed"
    assert status["plan"]["mutation_required"] is False

    noop = asyncio.run(
        restarted.provision(_context("operation-after-restart"), "initial-setup", {})
    )
    assert noop["outcome"] is None
    assert commands.activations == []
