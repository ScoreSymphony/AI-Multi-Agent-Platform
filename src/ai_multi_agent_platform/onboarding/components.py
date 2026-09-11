"""Provider-neutral component discovery, compatibility and setup-profile primitives.

Issue #799 deliberately keeps this layer smaller than the canonical owner domains.  It describes
what an installed adapter/provider can do and whether it fits the current environment; it does not
install components, release secrets, authorize actions or replace the Registry/Marketplace.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

SETUP_PROFILE_SCHEMA_VERSION = "1"


class ComponentCategory(StrEnum):
    """First-run component boundaries that already map to canonical platform seams."""

    ORCHESTRATOR = "orchestrator"
    MODEL_PROVIDER = "model_provider"
    EXECUTOR = "executor"
    MEMORY_KNOWLEDGE = "memory_knowledge"
    TOOLS_MCP = "tools_mcp"
    STORAGE = "storage"
    COMPUTE = "compute"


class ComponentLifecycle(StrEnum):
    """Research/evaluation classification, independent from Registry trust."""

    RECOMMENDED = "recommended"
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


class ComponentAvailability(StrEnum):
    """Observed discovery state, not an authorization decision."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INSTALLATION_REQUIRED = "installation_required"


class RequirementKind(StrEnum):
    """Technical requirement classes understood by the compatibility resolver."""

    HARDWARE = "hardware"
    RUNTIME = "runtime"
    CAPABILITY = "capability"


class CompatibilityState(StrEnum):
    """Technical compatibility outcome exposed to first-run clients."""

    COMPATIBLE = "compatible"
    COMPATIBLE_WITH_CONSTRAINTS = "compatible_with_constraints"
    EXPERIMENTAL = "experimental"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    INSTALLATION_REQUIRED = "installation_required"
    INSUFFICIENT_HARDWARE = "insufficient_hardware"
    SECURITY_BLOCKER = "security_blocker"


class SetupMode(StrEnum):
    """Persisted first-run profile modes, not separate platform architectures."""

    AUTO = "auto"
    LOCAL = "local"
    MULTI_NODE = "multi_node"
    ADVANCED = "advanced"


class OverrideScope(StrEnum):
    """Canonical default precedence from broadest to most specific."""

    GLOBAL = "global"
    WORKSPACE = "workspace"
    AGENT = "agent"
    TASK = "task"


_SCOPE_PRECEDENCE = {
    OverrideScope.GLOBAL: 0,
    OverrideScope.WORKSPACE: 1,
    OverrideScope.AGENT: 2,
    OverrideScope.TASK: 3,
}


@dataclass(frozen=True, slots=True)
class ComponentRequirement:
    """One technical requirement expressed as a discoverable environment capability."""

    kind: RequirementKind
    capability: str
    required: bool = True
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("component requirement capability must not be blank")
        if self.detail is not None and not self.detail.strip():
            raise ValueError("component requirement detail must not be blank when provided")


@dataclass(frozen=True, slots=True)
class DiscoveredComponent:
    """Safe discovery metadata for one component behind a canonical platform seam."""

    component_id: str
    category: ComponentCategory
    display_name: str
    availability: ComponentAvailability
    lifecycle: ComponentLifecycle
    version: str | None = None
    capabilities: frozenset[str] = frozenset()
    requirements: tuple[ComponentRequirement, ...] = ()
    recommended_modes: frozenset[SetupMode] = frozenset()
    priority: int = 0
    source_ref: str | None = None
    metadata: Mapping[str, JsonValue] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ValueError("component_id must not be blank")
        if not self.display_name.strip():
            raise ValueError("component display_name must not be blank")
        if self.version is not None and not self.version.strip():
            raise ValueError("component version must not be blank when provided")
        if self.source_ref is not None and not self.source_ref.strip():
            raise ValueError("component source_ref must not be blank when provided")
        if any(not capability.strip() for capability in self.capabilities):
            raise ValueError("component capabilities must contain non-blank strings")
        safe_metadata = dict(self.metadata)
        _reject_secret_material(safe_metadata)
        object.__setattr__(self, "metadata", MappingProxyType(safe_metadata))

    @property
    def key(self) -> tuple[ComponentCategory, str]:
        return self.category, self.component_id

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "component_id": self.component_id,
            "category": self.category.value,
            "display_name": self.display_name,
            "availability": self.availability.value,
            "lifecycle": self.lifecycle.value,
            "version": self.version,
            "capabilities": sorted(self.capabilities),
            "requirements": [
                {
                    "kind": requirement.kind.value,
                    "capability": requirement.capability,
                    "required": requirement.required,
                    "detail": requirement.detail,
                }
                for requirement in self.requirements
            ],
            "recommended_modes": sorted(mode.value for mode in self.recommended_modes),
            "priority": self.priority,
            "source_ref": self.source_ref,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CompatibilityEnvironment:
    """Observed technical facts plus externally supplied canonical security blockers."""

    capabilities: frozenset[str] = frozenset()
    security_blocked_components: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if any(not capability.strip() for capability in self.capabilities):
            raise ValueError("environment capabilities must contain non-blank strings")
        if any(not component_id.strip() for component_id in self.security_blocked_components):
            raise ValueError("security blocker component IDs must contain non-blank strings")


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    component_id: str
    category: ComponentCategory
    state: CompatibilityState
    reasons: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "component_id": self.component_id,
            "category": self.category.value,
            "state": self.state.value,
            "reasons": list(self.reasons),
            "missing_requirements": list(self.missing_requirements),
        }


class ComponentCompatibilityResolver:
    """Resolve technical facts without becoming the platform trust/policy authority."""

    def resolve(
        self,
        component: DiscoveredComponent,
        environment: CompatibilityEnvironment,
    ) -> CompatibilityResult:
        if component.component_id in environment.security_blocked_components:
            return _compatibility_result(
                component,
                CompatibilityState.SECURITY_BLOCKER,
                "canonical security policy blocks this component",
            )
        if component.availability is ComponentAvailability.UNAVAILABLE:
            return _compatibility_result(
                component,
                CompatibilityState.UNAVAILABLE,
                "component is not currently reachable or usable",
            )
        if component.availability is ComponentAvailability.INSTALLATION_REQUIRED:
            return _compatibility_result(
                component,
                CompatibilityState.INSTALLATION_REQUIRED,
                "component or adapter requires installation",
            )

        missing_required = tuple(
            requirement
            for requirement in component.requirements
            if requirement.required and requirement.capability not in environment.capabilities
        )
        missing_optional = tuple(
            requirement
            for requirement in component.requirements
            if not requirement.required and requirement.capability not in environment.capabilities
        )
        if missing_required:
            state = _missing_requirement_state(missing_required)
            return CompatibilityResult(
                component_id=component.component_id,
                category=component.category,
                state=state,
                reasons=tuple(
                    requirement.detail or f"missing {requirement.kind.value} requirement"
                    for requirement in missing_required
                ),
                missing_requirements=tuple(
                    requirement.capability for requirement in missing_required
                ),
            )
        if component.lifecycle is ComponentLifecycle.DEPRECATED:
            return _compatibility_result(
                component,
                CompatibilityState.INCOMPATIBLE,
                "component is deprecated for new setup",
            )
        if component.lifecycle is ComponentLifecycle.EXPERIMENTAL:
            return _compatibility_result(
                component,
                CompatibilityState.EXPERIMENTAL,
                "component requires explicit evaluation/advanced selection",
            )
        if missing_optional:
            return CompatibilityResult(
                component_id=component.component_id,
                category=component.category,
                state=CompatibilityState.COMPATIBLE_WITH_CONSTRAINTS,
                reasons=tuple(
                    requirement.detail or f"optional {requirement.kind.value} capability missing"
                    for requirement in missing_optional
                ),
                missing_requirements=tuple(
                    requirement.capability for requirement in missing_optional
                ),
            )
        return _compatibility_result(component, CompatibilityState.COMPATIBLE)


@dataclass(frozen=True, slots=True)
class ComponentSelectionLayer:
    """One value-safe default/override layer owned by an existing canonical scope."""

    scope: OverrideScope
    selections: Mapping[ComponentCategory, str]

    def __post_init__(self) -> None:
        selections = dict(self.selections)
        if any(not component_id.strip() for component_id in selections.values()):
            raise ValueError("component selection IDs must not be blank")
        object.__setattr__(self, "selections", MappingProxyType(selections))


@dataclass(frozen=True, slots=True)
class SetupProfile:
    """Reversible first-run defaults.  It stores component IDs, never credentials."""

    profile_id: str
    mode: SetupMode
    defaults: Mapping[ComponentCategory, str]
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("setup profile_id must not be blank")
        if self.revision < 1:
            raise ValueError("setup profile revision must be positive")
        defaults = dict(self.defaults)
        if any(not component_id.strip() for component_id in defaults.values()):
            raise ValueError("setup profile component IDs must not be blank")
        object.__setattr__(self, "defaults", MappingProxyType(defaults))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "profile_id": self.profile_id,
            "mode": self.mode.value,
            "revision": self.revision,
            "defaults": {
                category.value: component_id
                for category, component_id in sorted(
                    self.defaults.items(), key=lambda item: item[0].value
                )
            },
        }


@dataclass(frozen=True, slots=True)
class SetupProfileState:
    profiles: tuple[SetupProfile, ...] = ()
    active_profile_id: str | None = None

    def __post_init__(self) -> None:
        profile_ids = [profile.profile_id for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("setup profile state contains duplicate profile IDs")
        if self.active_profile_id is not None and self.active_profile_id not in profile_ids:
            raise ValueError("active setup profile must exist in profile state")


class JsonSetupProfileStore:
    """Atomic value-safe profile persistence for first-run and later Settings changes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> SetupProfileState:
        if not self.path.exists():
            return SetupProfileState()
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _json_object(raw, "setup profile document")
        schema_version = _required_string(document, "schema_version")
        if schema_version != SETUP_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported setup profile schema version: "
                f"{schema_version!r}; expected {SETUP_PROFILE_SCHEMA_VERSION!r}"
            )
        raw_profiles = document.get("profiles")
        if not isinstance(raw_profiles, list):
            raise ValueError("setup profile document must contain a profiles list")
        profiles = tuple(_profile_from_json(item) for item in raw_profiles)
        active_profile_id = _optional_string(document, "active_profile_id")
        return SetupProfileState(
            profiles=tuple(sorted(profiles, key=lambda profile: profile.profile_id)),
            active_profile_id=active_profile_id,
        )

    def save(self, state: SetupProfileState) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": SETUP_PROFILE_SCHEMA_VERSION,
            "active_profile_id": state.active_profile_id,
            "profiles": [
                profile.to_json()
                for profile in sorted(state.profiles, key=lambda item: item.profile_id)
            ],
        }
        _atomic_write_json(self.path, document)


def resolve_component_selection(
    category: ComponentCategory,
    layers: Iterable[ComponentSelectionLayer],
) -> str | None:
    """Resolve Global -> Workspace -> Agent -> Task precedence deterministically."""

    selected: str | None = None
    selected_precedence = -1
    seen_scopes: set[OverrideScope] = set()
    for layer in layers:
        if layer.scope in seen_scopes:
            raise ValueError(f"duplicate component selection scope: {layer.scope.value}")
        seen_scopes.add(layer.scope)
        component_id = layer.selections.get(category)
        precedence = _SCOPE_PRECEDENCE[layer.scope]
        if component_id is not None and precedence > selected_precedence:
            selected = component_id
            selected_precedence = precedence
    return selected


def recommend_setup_profile(
    components: Iterable[DiscoveredComponent],
    environment: CompatibilityEnvironment,
    *,
    mode: SetupMode,
    profile_id: str,
    resolver: ComponentCompatibilityResolver | None = None,
) -> SetupProfile:
    """Build deterministic safe defaults without choosing experimental/deprecated components."""

    effective_resolver = resolver or ComponentCompatibilityResolver()
    compatible_states = {
        CompatibilityState.COMPATIBLE,
        CompatibilityState.COMPATIBLE_WITH_CONSTRAINTS,
    }
    candidates_by_category: dict[ComponentCategory, list[DiscoveredComponent]] = {}
    seen_keys: set[tuple[ComponentCategory, str]] = set()
    for component in components:
        if component.key in seen_keys:
            raise ValueError(
                "duplicate discovered component: "
                f"{component.category.value}/{component.component_id}"
            )
        seen_keys.add(component.key)
        if component.lifecycle not in {
            ComponentLifecycle.RECOMMENDED,
            ComponentLifecycle.SUPPORTED,
        }:
            continue
        result = effective_resolver.resolve(component, environment)
        if result.state not in compatible_states:
            continue
        if component.recommended_modes and mode not in component.recommended_modes:
            continue
        candidates_by_category.setdefault(component.category, []).append(component)

    defaults: dict[ComponentCategory, str] = {}
    for category, candidates in candidates_by_category.items():
        selected = min(
            candidates,
            key=lambda component: (
                0 if component.lifecycle is ComponentLifecycle.RECOMMENDED else 1,
                -component.priority,
                component.component_id,
            ),
        )
        defaults[category] = selected.component_id
    return SetupProfile(profile_id=profile_id, mode=mode, defaults=defaults)


def _compatibility_result(
    component: DiscoveredComponent,
    state: CompatibilityState,
    *reasons: str,
) -> CompatibilityResult:
    return CompatibilityResult(
        component_id=component.component_id,
        category=component.category,
        state=state,
        reasons=tuple(reasons),
    )


def _missing_requirement_state(
    missing: tuple[ComponentRequirement, ...],
) -> CompatibilityState:
    kinds = {requirement.kind for requirement in missing}
    if RequirementKind.HARDWARE in kinds:
        return CompatibilityState.INSUFFICIENT_HARDWARE
    if RequirementKind.RUNTIME in kinds:
        return CompatibilityState.INSTALLATION_REQUIRED
    return CompatibilityState.INCOMPATIBLE


def _profile_from_json(value: object) -> SetupProfile:
    data = _json_object(value, "setup profile")
    defaults_data = _json_object(data.get("defaults"), "setup profile defaults")
    defaults: dict[ComponentCategory, str] = {}
    for raw_category, raw_component_id in defaults_data.items():
        try:
            category = ComponentCategory(raw_category)
        except ValueError as exc:
            raise ValueError(f"unsupported setup profile category: {raw_category!r}") from exc
        if not isinstance(raw_component_id, str) or not raw_component_id.strip():
            raise ValueError("setup profile component IDs must be non-blank strings")
        defaults[category] = raw_component_id
    raw_revision = data.get("revision")
    if not isinstance(raw_revision, int) or isinstance(raw_revision, bool):
        raise ValueError("setup profile revision must be an integer")
    try:
        mode = SetupMode(_required_string(data, "mode"))
    except ValueError as exc:
        raise ValueError("unsupported setup profile mode") from exc
    return SetupProfile(
        profile_id=_required_string(data, "profile_id"),
        mode=mode,
        defaults=defaults,
        revision=raw_revision,
    )


def _atomic_write_json(path: Path, document: dict[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _reject_secret_material(value: JsonValue | Mapping[str, JsonValue]) -> None:
    sensitive = {
        "api_key",
        "apikey",
        "authorization",
        "bearer_token",
        "password",
        "secret",
        "token",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.casefold().replace("-", "_") in sensitive:
                raise ValueError(
                    f"component discovery metadata must not contain secret field: {key}"
                )
            _reject_secret_material(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_material(item)


def _json_object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _required_string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string when provided")
    return value


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
