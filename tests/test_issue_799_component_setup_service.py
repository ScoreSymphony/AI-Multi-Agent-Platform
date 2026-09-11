from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.onboarding import (
    COMPONENT_SETUP_RESOURCE_ID,
    CompatibilityEnvironment,
    ComponentAvailability,
    ComponentCategory,
    ComponentLifecycle,
    DiscoveredComponent,
    JsonSetupProfileStore,
    OnboardingComponentSetupService,
    SetupMode,
)


class MutableDiscovery:
    def __init__(
        self,
        components: tuple[DiscoveredComponent, ...],
        *,
        environment: CompatibilityEnvironment | None = None,
    ) -> None:
        self.components = components
        self.current_environment = environment or CompatibilityEnvironment()

    def discover(self) -> tuple[DiscoveredComponent, ...]:
        return self.components

    def environment(self) -> CompatibilityEnvironment:
        return self.current_environment


def _context() -> RequestContext:
    return RequestContext(
        request_id="issue-799-request",
        correlation_id="issue-799-correlation",
        idempotency_key="issue-799-command",
        actor=ActorContext(
            principal_ref="user-alice",
            owner_type="user",
            owner_id="user-alice",
            actor_type="human",
        ),
    )


def _component(
    component_id: str,
    category: ComponentCategory,
    *,
    lifecycle: ComponentLifecycle = ComponentLifecycle.SUPPORTED,
    availability: ComponentAvailability = ComponentAvailability.AVAILABLE,
    modes: frozenset[SetupMode] = frozenset(),
) -> DiscoveredComponent:
    return DiscoveredComponent(
        component_id=component_id,
        category=category,
        display_name=component_id,
        availability=availability,
        lifecycle=lifecycle,
        recommended_modes=modes,
    )


def _service(tmp_path: Path, discovery: MutableDiscovery) -> OnboardingComponentSetupService:
    return OnboardingComponentSetupService(
        discovery,
        JsonSetupProfileStore(tmp_path / "component-profiles.json"),
    )


def test_status_projects_discovery_compatibility_and_profile_modes(tmp_path: Path) -> None:
    discovery = MutableDiscovery(
        (
            _component(
                "reference-executor",
                ComponentCategory.EXECUTOR,
                lifecycle=ComponentLifecycle.RECOMMENDED,
            ),
            _component("local-files", ComponentCategory.STORAGE),
        )
    )
    service = _service(tmp_path, discovery)

    status = service.status()

    assert status["id"] == COMPONENT_SETUP_RESOURCE_ID
    assert status["active_profile_id"] is None
    assert status["available_setup_modes"] == [mode.value for mode in SetupMode]
    components = status["components"]
    assert isinstance(components, list)
    assert [item["component_id"] for item in components] == [
        "reference-executor",
        "local-files",
    ]
    assert all(item["compatibility"]["state"] == "compatible" for item in components)


def test_auto_profile_is_persisted_and_retry_safe_when_inputs_are_unchanged(tmp_path: Path) -> None:
    discovery = MutableDiscovery(
        (
            _component(
                "reference-executor",
                ComponentCategory.EXECUTOR,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL}),
            ),
            _component(
                "local-files",
                ComponentCategory.STORAGE,
                lifecycle=ComponentLifecycle.RECOMMENDED,
                modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL}),
            ),
        )
    )
    service = _service(tmp_path, discovery)
    payload = {"profile_id": "auto", "mode": "auto"}

    first = asyncio.run(service.save_profile(_context(), COMPONENT_SETUP_RESOURCE_ID, payload))
    second = asyncio.run(service.save_profile(_context(), COMPONENT_SETUP_RESOURCE_ID, payload))

    assert first["revision"] == 1
    assert second["revision"] == 1
    assert first["defaults"] == {
        "executor": "reference-executor",
        "storage": "local-files",
    }
    assert service.status()["active_profile_id"] == "auto"
    persisted = (tmp_path / "component-profiles.json").read_text(encoding="utf-8").casefold()
    assert "password" not in persisted
    assert "api_key" not in persisted
    assert "token" not in persisted


def test_profile_revision_increments_when_defaults_change(tmp_path: Path) -> None:
    discovery = MutableDiscovery(
        (
            _component("reference-executor", ComponentCategory.EXECUTOR),
            _component("forge", ComponentCategory.EXECUTOR),
        )
    )
    service = _service(tmp_path, discovery)

    first = asyncio.run(
        service.save_profile(
            _context(),
            COMPONENT_SETUP_RESOURCE_ID,
            {
                "profile_id": "advanced",
                "mode": "advanced",
                "defaults": {"executor": "reference-executor"},
            },
        )
    )
    second = asyncio.run(
        service.save_profile(
            _context(),
            COMPONENT_SETUP_RESOURCE_ID,
            {
                "profile_id": "advanced",
                "mode": "advanced",
                "defaults": {"executor": "forge"},
            },
        )
    )

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert second["defaults"] == {"executor": "forge"}


def test_advanced_profile_can_select_experimental_component_explicitly(tmp_path: Path) -> None:
    discovery = MutableDiscovery(
        (
            _component(
                "agent-sandbox",
                ComponentCategory.EXECUTOR,
                lifecycle=ComponentLifecycle.EXPERIMENTAL,
            ),
        )
    )
    service = _service(tmp_path, discovery)

    result = asyncio.run(
        service.save_profile(
            _context(),
            COMPONENT_SETUP_RESOURCE_ID,
            {
                "profile_id": "advanced",
                "mode": "advanced",
                "defaults": {"executor": "agent-sandbox"},
            },
        )
    )

    assert result["defaults"] == {"executor": "agent-sandbox"}


def test_non_advanced_profile_cannot_promote_experimental_component(tmp_path: Path) -> None:
    discovery = MutableDiscovery(
        (
            _component(
                "agent-sandbox",
                ComponentCategory.EXECUTOR,
                lifecycle=ComponentLifecycle.EXPERIMENTAL,
            ),
        )
    )
    service = _service(tmp_path, discovery)

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.save_profile(
                _context(),
                COMPONENT_SETUP_RESOURCE_ID,
                {
                    "profile_id": "local",
                    "mode": "local",
                    "defaults": {"executor": "agent-sandbox"},
                },
            )
        )

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_security_blocked_component_cannot_be_saved_even_in_advanced_mode(tmp_path: Path) -> None:
    discovery = MutableDiscovery(
        (_component("remote-executor", ComponentCategory.EXECUTOR),),
        environment=CompatibilityEnvironment(
            security_blocked_components=frozenset({"remote-executor"})
        ),
    )
    service = _service(tmp_path, discovery)

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.save_profile(
                _context(),
                COMPONENT_SETUP_RESOURCE_ID,
                {
                    "profile_id": "advanced",
                    "mode": "advanced",
                    "defaults": {"executor": "remote-executor"},
                },
            )
        )

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
    assert exc_info.value.details["compatibility"] == "security_blocker"


def test_removed_provider_remains_persisted_but_cannot_be_reactivated(tmp_path: Path) -> None:
    discovery = MutableDiscovery((_component("forge", ComponentCategory.EXECUTOR),))
    service = _service(tmp_path, discovery)
    asyncio.run(
        service.save_profile(
            _context(),
            COMPONENT_SETUP_RESOURCE_ID,
            {
                "profile_id": "forge-profile",
                "mode": "advanced",
                "defaults": {"executor": "forge"},
            },
        )
    )
    discovery.components = ()

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.select_profile(
                _context(),
                COMPONENT_SETUP_RESOURCE_ID,
                {"profile_id": "forge-profile"},
            )
        )

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
    profiles = service.status()["profiles"]
    assert isinstance(profiles, list)
    assert profiles[0]["defaults"] == {"executor": "forge"}


def test_new_provider_is_visible_without_profile_migration(tmp_path: Path) -> None:
    discovery = MutableDiscovery((_component("reference", ComponentCategory.EXECUTOR),))
    service = _service(tmp_path, discovery)
    asyncio.run(
        service.save_profile(
            _context(),
            COMPONENT_SETUP_RESOURCE_ID,
            {
                "profile_id": "reference",
                "mode": "advanced",
                "defaults": {"executor": "reference"},
            },
        )
    )
    discovery.components = (
        _component("reference", ComponentCategory.EXECUTOR),
        _component("forge", ComponentCategory.EXECUTOR),
    )

    status = service.status()

    components = status["components"]
    assert isinstance(components, list)
    assert [item["component_id"] for item in components] == ["forge", "reference"]
    profiles = status["profiles"]
    assert isinstance(profiles, list)
    assert profiles[0]["defaults"] == {"executor": "reference"}


def test_payload_rejects_secret_or_unknown_fields_instead_of_persisting_them(tmp_path: Path) -> None:
    discovery = MutableDiscovery((_component("reference", ComponentCategory.EXECUTOR),))
    service = _service(tmp_path, discovery)

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.save_profile(
                _context(),
                COMPONENT_SETUP_RESOURCE_ID,
                {
                    "profile_id": "bad",
                    "mode": "advanced",
                    "defaults": {"executor": "reference"},
                    "api_key": "must-not-be-persisted",
                },
            )
        )

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
    assert not (tmp_path / "component-profiles.json").exists()
