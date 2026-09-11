"""First-run component discovery, compatibility projection and profile management.

The service composes the provider-neutral primitives from :mod:`onboarding.components` without
owning installation, trust, authorization, secrets or any canonical provider lifecycle.
"""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext

from .components import (
    CompatibilityEnvironment,
    CompatibilityState,
    ComponentCategory,
    ComponentCompatibilityResolver,
    DiscoveredComponent,
    JsonSetupProfileStore,
    SetupMode,
    SetupProfile,
    SetupProfileState,
    recommend_setup_profile,
)

COMPONENT_SETUP_RESOURCE_ID = "component-setup"
ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND = "onboarding.save-component-profile"
ONBOARDING_SELECT_COMPONENT_PROFILE_COMMAND = "onboarding.select-component-profile"


class ComponentDiscoverySource(Protocol):
    """Read-only discovery seam supplied by the outer runtime composition."""

    def discover(self) -> tuple[DiscoveredComponent, ...]: ...

    def environment(self) -> CompatibilityEnvironment: ...


class OnboardingComponentSetupService:
    """Project discovery facts and persist reversible, value-safe setup profiles."""

    def __init__(
        self,
        discovery: ComponentDiscoverySource,
        profile_store: JsonSetupProfileStore,
        *,
        resolver: ComponentCompatibilityResolver | None = None,
    ) -> None:
        self.discovery = discovery
        self.profile_store = profile_store
        self.resolver = resolver or ComponentCompatibilityResolver()
        self._state = self.profile_store.load()

    def status(self) -> dict[str, JsonValue]:
        """Return current discovery/compatibility facts plus persisted profile state."""

        components = self._components()
        environment = self.discovery.environment()
        projected_components: list[JsonValue] = []
        for component in components:
            item = component.to_json()
            item["compatibility"] = self.resolver.resolve(component, environment).to_json()
            projected_components.append(item)
        return {
            "id": COMPONENT_SETUP_RESOURCE_ID,
            "type": "component_setup",
            "components": projected_components,
            "profiles": [profile.to_json() for profile in self._state.profiles],
            "active_profile_id": self._state.active_profile_id,
            "available_setup_modes": [mode.value for mode in SetupMode],
        }

    async def save_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Create/update one reversible profile after compatibility validation."""

        del context
        self._require_resource_ref(resource_ref)
        _reject_unknown_fields(
            payload,
            allowed=frozenset({"profile_id", "mode", "defaults", "activate"}),
        )
        profile_id = _required_string(payload, "profile_id")
        mode = _setup_mode(payload)
        activate = _optional_bool(payload, "activate", default=True)
        defaults_value = payload.get("defaults")
        if defaults_value is None:
            if mode is SetupMode.ADVANCED:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "advanced component setup requires explicit defaults",
                )
            candidate = recommend_setup_profile(
                self._components(),
                self.discovery.environment(),
                mode=mode,
                profile_id=profile_id,
                resolver=self.resolver,
            )
        else:
            candidate = SetupProfile(
                profile_id=profile_id,
                mode=mode,
                defaults=_component_defaults(defaults_value),
            )
            self._validate_explicit_profile(candidate)

        existing = next(
            (profile for profile in self._state.profiles if profile.profile_id == profile_id),
            None,
        )
        if existing is not None:
            if existing.mode == candidate.mode and existing.defaults == candidate.defaults:
                candidate = existing
            else:
                candidate = SetupProfile(
                    profile_id=candidate.profile_id,
                    mode=candidate.mode,
                    defaults=candidate.defaults,
                    revision=existing.revision + 1,
                )
        profiles = tuple(
            sorted(
                (
                    *(profile for profile in self._state.profiles if profile.profile_id != profile_id),
                    candidate,
                ),
                key=lambda profile: profile.profile_id,
            )
        )
        active_profile_id = profile_id if activate else self._state.active_profile_id
        self._state = SetupProfileState(
            profiles=profiles,
            active_profile_id=active_profile_id,
        )
        self.profile_store.save(self._state)
        return _profile_result(candidate, active=active_profile_id == profile_id)

    async def select_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Select an existing profile without mutating its defaults."""

        del context
        self._require_resource_ref(resource_ref)
        _reject_unknown_fields(payload, allowed=frozenset({"profile_id"}))
        profile_id = _required_string(payload, "profile_id")
        profile = next(
            (item for item in self._state.profiles if item.profile_id == profile_id),
            None,
        )
        if profile is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"component setup profile not found: {profile_id}",
            )
        self._validate_explicit_profile(profile, allow_recommended_sparse=True)
        self._state = SetupProfileState(
            profiles=self._state.profiles,
            active_profile_id=profile_id,
        )
        self.profile_store.save(self._state)
        return _profile_result(profile, active=True)

    def _components(self) -> tuple[DiscoveredComponent, ...]:
        components = tuple(
            sorted(
                self.discovery.discover(),
                key=lambda item: (item.category.value, item.component_id),
            )
        )
        keys = [component.key for component in components]
        if len(keys) != len(set(keys)):
            raise ValueError("component discovery returned duplicate category/component IDs")
        return components

    def _validate_explicit_profile(
        self,
        profile: SetupProfile,
        *,
        allow_recommended_sparse: bool = False,
    ) -> None:
        components = {component.key: component for component in self._components()}
        environment = self.discovery.environment()
        for category, component_id in profile.defaults.items():
            component = components.get((category, component_id))
            if component is None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "selected component is not currently discovered",
                    details={
                        "category": category.value,
                        "component_id": component_id,
                    },
                )
            result = self.resolver.resolve(component, environment)
            allowed_states = {
                CompatibilityState.COMPATIBLE,
                CompatibilityState.COMPATIBLE_WITH_CONSTRAINTS,
            }
            if profile.mode is SetupMode.ADVANCED:
                allowed_states.add(CompatibilityState.EXPERIMENTAL)
            if result.state not in allowed_states:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "selected component is not compatible with the current environment",
                    details={
                        "category": category.value,
                        "component_id": component_id,
                        "compatibility": result.state.value,
                        "reasons": list(result.reasons),
                    },
                )
        if not profile.defaults and profile.mode is SetupMode.ADVANCED and not allow_recommended_sparse:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "advanced component setup requires at least one explicit component selection",
            )

    @staticmethod
    def _require_resource_ref(resource_ref: str) -> None:
        if resource_ref != COMPONENT_SETUP_RESOURCE_ID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"component setup requires resource_ref={COMPONENT_SETUP_RESOURCE_ID!r}",
            )


def _profile_result(profile: SetupProfile, *, active: bool) -> dict[str, JsonValue]:
    return {
        "id": profile.profile_id,
        "type": "component_setup_profile",
        "mode": profile.mode.value,
        "revision": profile.revision,
        "defaults": {
            category.value: component_id for category, component_id in profile.defaults.items()
        },
        "active": active,
    }


def _component_defaults(value: JsonValue) -> dict[ComponentCategory, str]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "component defaults must be an object")
    defaults: dict[ComponentCategory, str] = {}
    for raw_category, raw_component_id in value.items():
        try:
            category = ComponentCategory(raw_category)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported component setup category: {raw_category}",
            ) from exc
        if not isinstance(raw_component_id, str) or not raw_component_id.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "component defaults must contain non-blank component IDs",
            )
        defaults[category] = raw_component_id
    return defaults


def _setup_mode(payload: dict[str, JsonValue]) -> SetupMode:
    raw_mode = _required_string(payload, "mode")
    try:
        return SetupMode(raw_mode)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported component setup mode: {raw_mode}",
        ) from exc


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_bool(payload: dict[str, JsonValue], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a boolean")
    return value


def _reject_unknown_fields(payload: dict[str, JsonValue], *, allowed: frozenset[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "component setup payload contains unsupported fields",
            details={"unknown_fields": unknown},
        )
