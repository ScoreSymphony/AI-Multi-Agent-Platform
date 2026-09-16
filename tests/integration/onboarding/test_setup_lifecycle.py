from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.onboarding import (
    BrowserFirstSetupService,
    CompatibilityEnvironment,
    ComponentAvailability,
    ComponentCategory,
    ComponentLifecycle,
    DiscoveredComponent,
    JsonSetupProfileStore,
    JsonSetupSessionStore,
    OnboardingComponentSetupService,
    SetupMode,
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
            DiscoveredComponent(
                component_id="local-model",
                category=ComponentCategory.MODEL_PROVIDER,
                display_name="Local Model",
                availability=ComponentAvailability.AVAILABLE,
                lifecycle=ComponentLifecycle.SUPPORTED,
                recommended_modes=frozenset({SetupMode.AUTO, SetupMode.LOCAL}),
                source_ref="model-provider:openai-compatible",
            ),
        )

    def environment(self) -> CompatibilityEnvironment:
        return CompatibilityEnvironment()


class _OnboardingStatus:
    def __init__(self, state: str = "needs_project") -> None:
        self.state = state

    def status(self, context: RequestContext):
        del context
        return {
            "id": "first-run",
            "type": "onboarding_status",
            "state": self.state,
            "local_model_count": 1,
            "self_hosted_model_count": 0,
        }

    async def status_async(self, context: RequestContext):
        return self.status(context)


def _context(*, idempotency_key: str | None = None) -> RequestContext:
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


def _service(tmp_path, *, state: str = "needs_project"):
    component_setup = OnboardingComponentSetupService(
        _Discovery(),
        JsonSetupProfileStore(tmp_path / "component-profiles.json"),
    )
    asyncio.run(
        component_setup.save_profile(
            _context(),
            "component-setup",
            {"profile_id": "first-run", "mode": "auto", "activate": True},
        )
    )
    lifecycle = BrowserFirstSetupService(
        component_setup,
        _OnboardingStatus(state),  # type: ignore[arg-type]
        JsonSetupSessionStore(tmp_path / "setup-sessions.json"),
    )
    return lifecycle


def test_setup_status_reuses_available_components_and_derives_canonical_readiness(tmp_path) -> None:
    lifecycle = _service(tmp_path)

    status = lifecycle.status(_context())

    assert status["active_profile_id"] == "first-run"
    assert status["readiness"]["ready"] is False
    assert status["readiness"]["canonical_onboarding_state"] == "needs_project"
    actions = status["plan"]["actions"]
    assert {action["kind"] for action in actions} == {"reuse"}
    assert {action["state"] for action in actions} == {"completed"}
    assert status["plan"]["mutation_required"] is False


def test_setup_session_navigation_is_persistent_and_secret_fields_are_rejected(tmp_path) -> None:
    lifecycle = _service(tmp_path)
    context = _context()

    updated = asyncio.run(
        lifecycle.update_session(
            context,
            "initial-setup",
            {"current_step": "components", "registry_items": []},
        )
    )
    assert updated["current_step"] == "components"

    restored = _service(tmp_path)
    assert restored.status(context)["current_step"] == "components"

    with pytest.raises(ContractError):
        asyncio.run(
            restored.update_session(
                context,
                "initial-setup",
                {"password": "must-never-enter-setup-state"},
            )
        )


def test_validation_enters_ready_only_when_canonical_onboarding_is_ready(tmp_path) -> None:
    lifecycle = _service(tmp_path, state="ready_for_task")
    context = _context(idempotency_key="validate-1")

    validated = asyncio.run(lifecycle.validate(context, "initial-setup", {}))

    assert validated["current_step"] == "ready"
    assert validated["readiness"]["ready"] is True
    assert validated["readiness"]["dashboard_allowed"] is True
