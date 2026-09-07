"""Runtime registry for backend-neutral tool capabilities."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue, ProviderDescriptor

from .provider import CapabilityToolProvider
from .types import (
    CapabilityCompatibilityRequest,
    CapabilityDiscoveryRequest,
    CapabilityRegistration,
    CapabilitySpec,
    PolicyDecision,
)

type CapabilityDiscoveryPolicyHook = Callable[
    [CapabilityDiscoveryRequest, CapabilitySpec], Awaitable[PolicyDecision]
]


class CapabilityRegistry:
    """Register, discover and resolve capability providers without backend coupling."""

    def __init__(self) -> None:
        self._providers: dict[str, CapabilityToolProvider] = {}
        self._registrations: dict[tuple[str, str], list[CapabilityRegistration]] = defaultdict(list)

    async def register_provider(self, provider: CapabilityToolProvider) -> None:
        provider_id = provider.descriptor.provider_id
        if provider_id in self._providers:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"provider {provider_id!r} is already registered",
                provider_id=provider_id,
            )

        registrations = await provider.capability_registrations()
        for registration in registrations:
            if registration.provider_id != provider_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "capability registration provider_id does not match provider descriptor",
                    provider_id=provider_id,
                )

        pending: dict[tuple[str, str], CapabilityRegistration] = {}
        for registration in registrations:
            key = (registration.capability.capability_id, registration.capability.version)
            duplicate = pending.get(key)
            if duplicate is not None:
                if duplicate.capability != registration.capability:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        (
                            "conflicting capability definitions returned by provider "
                            f"{provider_id!r} for {registration.capability.capability_id!r} "
                            f"version {registration.capability.version!r}"
                        ),
                        provider_id=provider_id,
                    )
                raise ContractError(
                    ErrorCode.CONFLICT,
                    (
                        "duplicate capability registration returned by provider "
                        f"{provider_id!r} for {registration.capability.capability_id!r} "
                        f"version {registration.capability.version!r}"
                    ),
                    provider_id=provider_id,
                )
            pending[key] = registration
            for existing in self._registrations[key]:
                if existing.capability != registration.capability:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        (
                            "conflicting capability definition for "
                            f"{registration.capability.capability_id!r} "
                            f"version {registration.capability.version!r}"
                        ),
                        provider_id=provider_id,
                    )

        self._providers[provider_id] = provider
        for registration in registrations:
            key = (registration.capability.capability_id, registration.capability.version)
            self._registrations[key].append(registration)

    def unregister_provider(self, provider_id: str) -> None:
        if provider_id not in self._providers:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"provider {provider_id!r} is not registered",
                provider_id=provider_id,
            )
        del self._providers[provider_id]
        for key in tuple(self._registrations):
            remaining = [
                registration
                for registration in self._registrations[key]
                if registration.provider_id != provider_id
            ]
            if remaining:
                self._registrations[key] = remaining
            else:
                del self._registrations[key]

    async def refresh_health(self) -> None:
        """Refresh provider health into published capability snapshots."""

        for provider_id, provider in self._providers.items():
            health = await provider.health()
            available = provider.descriptor.available and health is not HealthStatus.UNAVAILABLE
            for key, registrations in tuple(self._registrations.items()):
                refreshed: list[CapabilityRegistration] = []
                for registration in registrations:
                    if registration.provider_id == provider_id:
                        refreshed.append(
                            replace(
                                registration,
                                capability=replace(
                                    registration.capability,
                                    health=health,
                                    available=available,
                                ),
                            )
                        )
                    else:
                        refreshed.append(registration)
                self._registrations[key] = refreshed

    def inventory_capabilities(
        self,
        *,
        include_unavailable: bool = True,
    ) -> tuple[CapabilitySpec, ...]:
        """Return canonical capability inventory with provider state aggregated per version.

        This administrative/read surface deliberately differs from ``list_capabilities``:
        permissions and worker placement describe whether one caller may use a capability,
        not whether the registered capability exists. Volatile availability/health is aggregated
        across equivalent providers so one unavailable registration cannot hide a usable provider.
        Invocation and policy discovery remain authoritative for actual use.
        """

        inventory: list[CapabilitySpec] = []
        for key in sorted(
            self._registrations,
            key=lambda item: (item[0], _version_display_key(item[1])),
        ):
            registrations = self._registrations[key]
            if not registrations:
                continue
            capability = _aggregate_capability_spec(registrations)
            if not include_unavailable and (
                not capability.available or capability.health is HealthStatus.UNAVAILABLE
            ):
                continue
            inventory.append(capability)
        return tuple(inventory)

    def inventory_providers(self) -> tuple[ProviderDescriptor, ...]:
        """Return stable public descriptors without exposing provider implementation objects."""

        return tuple(
            self._providers[provider_id].descriptor for provider_id in sorted(self._providers)
        )

    def list_capabilities(
        self,
        *,
        granted_permissions: frozenset[str] | None = None,
        available_worker_capabilities: frozenset[str] | None = None,
        include_unavailable: bool = False,
    ) -> tuple[CapabilitySpec, ...]:
        """List statically eligible inventory without claiming policy authorization.

        Agent/user-facing usable discovery should call :meth:`discover_capabilities`, which
        adds the replaceable policy decision hook while preserving this synchronous inventory
        API for administrative/internal callers.
        """

        granted_permissions = granted_permissions or frozenset()
        available_worker_capabilities = available_worker_capabilities or frozenset()
        seen: dict[tuple[str, str], CapabilitySpec] = {}

        for key, registrations in self._registrations.items():
            for registration in registrations:
                capability = registration.capability
                if not include_unavailable and (
                    not capability.available or capability.health is HealthStatus.UNAVAILABLE
                ):
                    continue
                if not set(capability.required_permissions).issubset(granted_permissions):
                    continue
                if not set(capability.required_worker_capabilities).issubset(
                    available_worker_capabilities
                ):
                    continue
                seen[key] = capability
                break

        return tuple(
            seen[key]
            for key in sorted(seen, key=lambda item: (item[0], _version_display_key(item[1])))
        )

    async def discover_capabilities(
        self,
        request: CapabilityDiscoveryRequest,
        *,
        policy_hook: CapabilityDiscoveryPolicyHook | None = None,
        include_unavailable: bool = False,
    ) -> tuple[CapabilitySpec, ...]:
        """Return capabilities usable in the caller's policy context.

        ``PolicyDecision.DENY`` removes a capability from discovery. ``REQUIRE_APPROVAL``
        remains discoverable because it is usable through the canonical approval path. The
        invocation pipeline always evaluates its own policy hook again before execution, so
        discovery never becomes an authorization bypass or a cached grant.
        """

        candidates = self.list_capabilities(
            granted_permissions=request.granted_permissions,
            available_worker_capabilities=request.available_worker_capabilities,
            include_unavailable=include_unavailable,
        )
        if policy_hook is None:
            return candidates

        visible: list[CapabilitySpec] = []
        for capability in candidates:
            decision = await policy_hook(request, capability)
            if decision is not PolicyDecision.DENY:
                visible.append(capability)
        return tuple(visible)

    def resolve(
        self,
        capability_id: str,
        *,
        version: str | None = None,
        compatibility: CapabilityCompatibilityRequest | None = None,
        granted_permissions: frozenset[str] | None = None,
        available_worker_capabilities: frozenset[str] | None = None,
    ) -> tuple[CapabilityRegistration, CapabilityToolProvider]:
        granted_permissions = granted_permissions or frozenset()
        available_worker_capabilities = available_worker_capabilities or frozenset()

        if version is not None and compatibility is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "exact version and compatibility request are mutually exclusive",
            )

        versions = {
            registered_version
            for registered_id, registered_version in self._registrations
            if registered_id == capability_id
        }
        if not versions:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"capability {capability_id!r} is not registered",
            )

        selected_version = self._select_version(
            capability_id,
            versions,
            version=version,
            compatibility=compatibility,
        )
        registrations = self._registrations[(capability_id, selected_version)]
        permission_eligible = [
            registration
            for registration in registrations
            if set(registration.capability.required_permissions).issubset(granted_permissions)
        ]
        if not permission_eligible:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"capability {capability_id!r} is not permitted for this invocation",
            )

        worker_eligible = [
            registration
            for registration in permission_eligible
            if set(registration.capability.required_worker_capabilities).issubset(
                available_worker_capabilities
            )
        ]
        if not worker_eligible:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"capability {capability_id!r} has no eligible worker/provider placement",
            )

        candidates = [
            registration
            for registration in worker_eligible
            if registration.capability.available
            and registration.capability.health is not HealthStatus.UNAVAILABLE
        ]
        if not candidates:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"capability {capability_id!r} has no available provider",
            )

        candidates.sort(key=lambda registration: (-registration.priority, registration.provider_id))
        if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
            raise ContractError(
                ErrorCode.CONFLICT,
                (
                    f"capability {capability_id!r} version {selected_version!r} "
                    "has multiple equally preferred providers"
                ),
                details={"providers": [candidate.provider_id for candidate in candidates]},
            )

        selected = candidates[0]
        return selected, self._providers[selected.provider_id]

    def _select_version(
        self,
        capability_id: str,
        versions: set[str],
        *,
        version: str | None,
        compatibility: CapabilityCompatibilityRequest | None,
    ) -> str:
        available_versions = sorted(versions, key=_version_display_key)
        if version is not None:
            if version not in versions:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    (
                        f"capability {capability_id!r} does not provide requested "
                        f"version {version!r}"
                    ),
                    details={"available_versions": cast(JsonValue, available_versions)},
                )
            return version

        if compatibility is not None:
            return self._select_compatible_version(
                capability_id,
                available_versions,
                compatibility,
            )

        if len(available_versions) == 1:
            return available_versions[0]

        normalized = _require_unambiguous_numeric_versions(
            capability_id,
            available_versions,
            purpose="automatic latest-version selection",
        )
        return max(available_versions, key=lambda candidate: normalized[candidate])

    def _select_compatible_version(
        self,
        capability_id: str,
        available_versions: list[str],
        compatibility: CapabilityCompatibilityRequest,
    ) -> str:
        required_features = set(compatibility.required_features)
        feature_eligible = [
            version
            for version in available_versions
            if required_features.issubset(
                set(self._registrations[(capability_id, version)][0].capability.features)
            )
        ]
        if not feature_eligible:
            raise _no_compatible_version_error(
                capability_id,
                available_versions,
                compatibility,
            )

        normalized = _require_unambiguous_numeric_versions(
            capability_id,
            feature_eligible,
            purpose="compatibility resolution",
        )
        minimum = (
            _numeric_version_key(compatibility.minimum_version)
            if compatibility.minimum_version is not None
            else None
        )
        maximum = (
            _numeric_version_key(compatibility.maximum_version)
            if compatibility.maximum_version is not None
            else None
        )

        matches: list[str] = []
        for candidate in feature_eligible:
            candidate_key = normalized[candidate]
            if minimum is not None:
                if candidate_key < minimum or (
                    candidate_key == minimum and not compatibility.include_minimum
                ):
                    continue
            if maximum is not None:
                if candidate_key > maximum or (
                    candidate_key == maximum and not compatibility.include_maximum
                ):
                    continue
            matches.append(candidate)

        if not matches:
            raise _no_compatible_version_error(
                capability_id,
                available_versions,
                compatibility,
            )
        return max(matches, key=lambda candidate: normalized[candidate])


def _aggregate_capability_spec(
    registrations: list[CapabilityRegistration],
) -> CapabilitySpec:
    """Aggregate volatile provider state while preserving one canonical contract definition."""

    base = registrations[0].capability
    usable = [
        registration.capability
        for registration in registrations
        if registration.capability.available
        and registration.capability.health is not HealthStatus.UNAVAILABLE
    ]
    candidates = usable or [registration.capability for registration in registrations]
    health_rank = {
        HealthStatus.HEALTHY: 3,
        HealthStatus.DEGRADED: 2,
        HealthStatus.UNKNOWN: 1,
        HealthStatus.UNAVAILABLE: 0,
    }
    representative = max(candidates, key=lambda capability: health_rank[capability.health])
    return replace(
        base,
        health=representative.health,
        available=bool(usable),
    )


def _no_compatible_version_error(
    capability_id: str,
    available_versions: list[str],
    compatibility: CapabilityCompatibilityRequest,
) -> ContractError:
    details: dict[str, JsonValue] = {
        "available_versions": cast(JsonValue, available_versions),
        "required_features": cast(JsonValue, list(compatibility.required_features)),
        "include_minimum": compatibility.include_minimum,
        "include_maximum": compatibility.include_maximum,
    }
    if compatibility.minimum_version is not None:
        details["minimum_version"] = compatibility.minimum_version
    if compatibility.maximum_version is not None:
        details["maximum_version"] = compatibility.maximum_version
    return ContractError(
        ErrorCode.UNSUPPORTED_CAPABILITY,
        f"capability {capability_id!r} has no version compatible with the request",
        details=details,
    )


def _require_unambiguous_numeric_versions(
    capability_id: str,
    versions: list[str],
    *,
    purpose: str,
) -> dict[str, tuple[int, int, int]]:
    normalized: dict[str, tuple[int, int, int]] = {}
    by_key: dict[tuple[int, int, int], str] = {}
    opaque: list[str] = []

    for version in versions:
        key = _try_numeric_version_key(version)
        if key is None:
            opaque.append(version)
            continue
        previous = by_key.get(key)
        if previous is not None and previous != version:
            raise ContractError(
                ErrorCode.CONFLICT,
                (
                    f"capability {capability_id!r} has ambiguous versions {previous!r} and "
                    f"{version!r} for {purpose}; request an exact version"
                ),
                details={"ambiguous_versions": [previous, version]},
            )
        by_key[key] = version
        normalized[version] = key

    if opaque:
        raise ContractError(
            ErrorCode.CONFLICT,
            (
                f"capability {capability_id!r} contains versions without canonical numeric "
                f"compatibility semantics for {purpose}; request an exact version"
            ),
            details={"opaque_versions": cast(JsonValue, sorted(opaque))},
        )
    return normalized


def _try_numeric_version_key(version: str) -> tuple[int, int, int] | None:
    parts = version.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        return None
    values = [int(part) for part in parts]
    values.extend([0] * (3 - len(values)))
    return values[0], values[1], values[2]


def _numeric_version_key(version: str) -> tuple[int, int, int]:
    key = _try_numeric_version_key(version)
    if key is None:
        raise ValueError("compatibility request contains a non-numeric version")
    return key


def _version_display_key(version: str) -> tuple[int, tuple[int, int, int], str]:
    """Return a deterministic inventory sort key without inferring opaque compatibility."""

    numeric = _try_numeric_version_key(version)
    if numeric is not None:
        return (0, numeric, version)
    return (1, (0, 0, 0), version)
