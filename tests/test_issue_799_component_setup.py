from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.onboarding.components import (
    CompatibilityEnvironment,
    CompatibilityState,
    ComponentAvailability,
    ComponentCategory,
    ComponentCompatibilityResolver,
    ComponentLifecycle,
    ComponentRequirement,
    ComponentSelectionLayer,
    DiscoveredComponent,
    JsonSetupProfileStore,
    OverrideScope,
    RequirementKind,
    SetupMode,
    SetupProfile,
    SetupProfileState,
    recommend_setup_profile,
    resolve_component_selection,
)


def _component(
    component_id: str,
    category: ComponentCategory,
    *,
    lifecycle: ComponentLifecycle = ComponentLifecycle.SUPPORTED,
    availability: ComponentAvailability = ComponentAvailability.AVAILABLE,
    requirements: tuple[ComponentRequirement, ...] = (),
    modes: frozenset[SetupMode] = frozenset(),
    priority: int = 0,
) -> DiscoveredComponent:
    return DiscoveredComponent(
        component_id=component_id,
        category=category,
        display_name=component_id,
        availability=availability,
        lifecycle=lifecycle,
        requirements=requirements,
        recommended_modes=modes,
        priority=priority,
    )


def test_compatibility_distinguishes_hardware_runtime_and_capability_gaps() -> None:
    resolver = ComponentCompatibilityResolver()
    environment = CompatibilityEnvironment(capabilities=frozenset({"linux"}))

    hardware = _component(
        "gpu-model-provider",
        ComponentCategory.MODEL_PROVIDER,
        requirements=(
            ComponentRequirement(RequirementKind.HARDWARE, "gpu:cuda", detail="CUDA GPU required"),
        ),
    )
    runtime = _component(
        "container-executor",
        ComponentCategory.EXECUTOR,
        requirements=(
            ComponentRequirement(
                RequirementKind.RUNTIME,
                "runtime:container",
                detail="container runtime required",
            ),
        ),
    )
    capability = _component(
        "tool-aware-orchestrator",
        ComponentCategory.ORCHESTRATOR,
        requirements=(ComponentRequirement(RequirementKind.CAPABILITY, "tools:canonical"),),
    )

    assert resolver.resolve(hardware, environment).state is CompatibilityState.INSUFFICIENT_HARDWARE
    assert resolver.resolve(runtime, environment).state is CompatibilityState.INSTALLATION_REQUIRED
    assert resolver.resolve(capability, environment).state is CompatibilityState.INCOMPATIBLE


def test_compatibility_keeps_security_policy_external_but_fail_closed() -> None:
    component = _component("remote-executor", ComponentCategory.EXECUTOR)
    environment = CompatibilityEnvironment(
        security_blocked_components=frozenset({"remote-executor"})
    )

    result = ComponentCompatibilityResolver().resolve(component, environment)

    assert result.state is CompatibilityState.SECURITY_BLOCKER
    assert "canonical security policy" in result.reasons[0]


def test_experimental_component_is_never_silently_promoted_to_compatible() -> None:
    component = _component(
        "bifrost",
        ComponentCategory.MODEL_PROVIDER,
        lifecycle=ComponentLifecycle.EXPERIMENTAL,
    )

    result = ComponentCompatibilityResolver().resolve(component, CompatibilityEnvironment())

    assert result.state is CompatibilityState.EXPERIMENTAL


def test_optional_requirement_produces_compatible_with_constraints() -> None:
    component = _component(
        "local-model",
        ComponentCategory.MODEL_PROVIDER,
        requirements=(
            ComponentRequirement(
                RequirementKind.HARDWARE,
                "gpu:cuda",
                required=False,
                detail="GPU improves throughput but CPU fallback is supported",
            ),
        ),
    )

    result = ComponentCompatibilityResolver().resolve(component, CompatibilityEnvironment())

    assert result.state is CompatibilityState.COMPATIBLE_WITH_CONSTRAINTS
    assert result.missing_requirements == ("gpu:cuda",)


def test_auto_profile_prefers_recommended_components_and_is_deterministic() -> None:
    components = (
        _component(
            "ollama",
            ComponentCategory.MODEL_PROVIDER,
            modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL}),
            priority=100,
        ),
        _component(
            "platform-model-provider",
            ComponentCategory.MODEL_PROVIDER,
            lifecycle=ComponentLifecycle.RECOMMENDED,
            modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}),
        ),
        _component(
            "reference-executor",
            ComponentCategory.EXECUTOR,
            lifecycle=ComponentLifecycle.RECOMMENDED,
            modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL, SetupMode.MULTI_NODE}),
        ),
        _component(
            "agent-sandbox",
            ComponentCategory.EXECUTOR,
            lifecycle=ComponentLifecycle.EXPERIMENTAL,
            modes=frozenset({SetupMode.AUTO}),
            priority=999,
        ),
    )

    profile = recommend_setup_profile(
        reversed(components),
        CompatibilityEnvironment(),
        mode=SetupMode.AUTO,
        profile_id="auto-default",
    )

    assert profile.defaults == {
        ComponentCategory.MODEL_PROVIDER: "platform-model-provider",
        ComponentCategory.EXECUTOR: "reference-executor",
    }


def test_recommendation_never_selects_unavailable_or_security_blocked_component() -> None:
    components = (
        _component(
            "reference-executor",
            ComponentCategory.EXECUTOR,
            lifecycle=ComponentLifecycle.RECOMMENDED,
            availability=ComponentAvailability.UNAVAILABLE,
        ),
        _component(
            "forge",
            ComponentCategory.EXECUTOR,
            priority=10,
        ),
        _component(
            "unsafe-executor",
            ComponentCategory.EXECUTOR,
            lifecycle=ComponentLifecycle.RECOMMENDED,
            priority=999,
        ),
    )
    environment = CompatibilityEnvironment(
        security_blocked_components=frozenset({"unsafe-executor"})
    )

    profile = recommend_setup_profile(
        components,
        environment,
        mode=SetupMode.AUTO,
        profile_id="safe-default",
    )

    assert profile.defaults[ComponentCategory.EXECUTOR] == "forge"


def test_global_workspace_agent_task_precedence_is_explicit() -> None:
    layers = (
        ComponentSelectionLayer(
            OverrideScope.TASK,
            {ComponentCategory.MODEL_PROVIDER: "task-provider"},
        ),
        ComponentSelectionLayer(
            OverrideScope.GLOBAL,
            {ComponentCategory.MODEL_PROVIDER: "global-provider"},
        ),
        ComponentSelectionLayer(
            OverrideScope.AGENT,
            {ComponentCategory.MODEL_PROVIDER: "agent-provider"},
        ),
        ComponentSelectionLayer(
            OverrideScope.WORKSPACE,
            {ComponentCategory.MODEL_PROVIDER: "workspace-provider"},
        ),
    )

    selected = resolve_component_selection(ComponentCategory.MODEL_PROVIDER, layers)

    assert selected == "task-provider"


def test_duplicate_override_scope_fails_closed() -> None:
    layers = (
        ComponentSelectionLayer(
            OverrideScope.GLOBAL,
            {ComponentCategory.EXECUTOR: "reference"},
        ),
        ComponentSelectionLayer(
            OverrideScope.GLOBAL,
            {ComponentCategory.EXECUTOR: "forge"},
        ),
    )

    with pytest.raises(ValueError, match="duplicate component selection scope"):
        resolve_component_selection(ComponentCategory.EXECUTOR, layers)


def test_profile_store_round_trips_value_safe_defaults(tmp_path: Path) -> None:
    store = JsonSetupProfileStore(tmp_path / "setup-profiles.json")
    profile = SetupProfile(
        profile_id="local",
        mode=SetupMode.LOCAL,
        defaults={
            ComponentCategory.MODEL_PROVIDER: "platform-model-provider",
            ComponentCategory.EXECUTOR: "reference-executor",
            ComponentCategory.STORAGE: "local-files",
        },
    )

    store.save(SetupProfileState(profiles=(profile,), active_profile_id="local"))
    restored = store.load()

    assert restored == SetupProfileState(profiles=(profile,), active_profile_id="local")
    persisted = (tmp_path / "setup-profiles.json").read_text(encoding="utf-8").casefold()
    assert "password" not in persisted
    assert "api_key" not in persisted
    assert "bearer_token" not in persisted


def test_profile_store_rejects_unknown_category(tmp_path: Path) -> None:
    path = tmp_path / "setup-profiles.json"
    path.write_text(
        """{
  "schema_version": "1",
  "active_profile_id": "broken",
  "profiles": [
    {
      "profile_id": "broken",
      "mode": "auto",
      "revision": 1,
      "defaults": {"workflow_engine": "temporal"}
    }
  ]
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported setup profile category"):
        JsonSetupProfileStore(path).load()


def test_discovery_metadata_rejects_plaintext_secret_fields_recursively() -> None:
    with pytest.raises(ValueError, match="must not contain secret field"):
        DiscoveredComponent(
            component_id="remote-model",
            category=ComponentCategory.MODEL_PROVIDER,
            display_name="Remote model",
            availability=ComponentAvailability.AVAILABLE,
            lifecycle=ComponentLifecycle.SUPPORTED,
            metadata={"connection": {"api_key": "must-not-be-here"}},
        )
