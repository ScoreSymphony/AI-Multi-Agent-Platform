from __future__ import annotations

import asyncio
from pathlib import Path

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
    def __init__(self, components: tuple[DiscoveredComponent, ...]) -> None:
        self.components = components

    def discover(self) -> tuple[DiscoveredComponent, ...]:
        return self.components

    def environment(self) -> CompatibilityEnvironment:
        return CompatibilityEnvironment()


def _component(
    component_id: str,
    *,
    lifecycle: ComponentLifecycle = ComponentLifecycle.SUPPORTED,
    modes: frozenset[SetupMode] = frozenset(),
) -> DiscoveredComponent:
    return DiscoveredComponent(
        component_id=component_id,
        category=ComponentCategory.EXECUTOR,
        display_name=component_id,
        availability=ComponentAvailability.AVAILABLE,
        lifecycle=lifecycle,
        recommended_modes=modes,
    )


def _context() -> RequestContext:
    return RequestContext(
        request_id="issue-799-fallback-request",
        correlation_id="issue-799-fallback-correlation",
        idempotency_key="issue-799-fallback-command",
        actor=ActorContext(
            principal_ref="user-alice",
            owner_type="user",
            owner_id="user-alice",
            actor_type="human",
        ),
    )


def test_active_profile_reports_degraded_state_and_safe_auto_fallback(tmp_path: Path) -> None:
    reference = _component(
        "reference-executor",
        lifecycle=ComponentLifecycle.RECOMMENDED,
        modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}),
    )
    forge = _component("forge")
    discovery = MutableDiscovery((reference, forge))
    service = OnboardingComponentSetupService(
        discovery,
        JsonSetupProfileStore(tmp_path / "component-profiles.json"),
    )

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

    discovery.components = (reference,)
    status = service.status()

    assert status["active_profile_id"] == "forge-profile"
    profiles = status["profiles"]
    assert isinstance(profiles, list)
    profile = profiles[0]
    assert isinstance(profile, dict)
    validation = profile["validation"]
    assert isinstance(validation, dict)
    assert validation["valid"] is False
    assert validation["issues"] == [
        {
            "category": "executor",
            "component_id": "forge",
            "compatibility": "unavailable",
            "reasons": ["selected component is not currently discovered"],
        }
    ]
    assert validation["fallback"] == {
        "mode": "auto",
        "defaults": {"executor": "reference-executor"},
    }

    persisted = JsonSetupProfileStore(tmp_path / "component-profiles.json").load()
    assert persisted.active_profile_id == "forge-profile"
    assert persisted.profiles[0].defaults == {ComponentCategory.EXECUTOR: "forge"}
