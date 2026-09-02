"""Runtime registry for backend-neutral tool capabilities."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus

from .provider import CapabilityToolProvider
from .types import CapabilityRegistration, CapabilitySpec


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

        for registration in registrations:
            key = (registration.capability.capability_id, registration.capability.version)
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

    def list_capabilities(
        self,
        *,
        granted_permissions: frozenset[str] | None = None,
        available_worker_capabilities: frozenset[str] | None = None,
        include_unavailable: bool = False,
    ) -> tuple[CapabilitySpec, ...]:
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
            seen[key] for key in sorted(seen, key=lambda item: (item[0], _version_key(item[1])))
        )

    def resolve(
        self,
        capability_id: str,
        *,
        version: str | None = None,
        granted_permissions: frozenset[str] | None = None,
        available_worker_capabilities: frozenset[str] | None = None,
    ) -> tuple[CapabilityRegistration, CapabilityToolProvider]:
        granted_permissions = granted_permissions or frozenset()
        available_worker_capabilities = available_worker_capabilities or frozenset()

        versions = sorted(
            {
                registered_version
                for registered_id, registered_version in self._registrations
                if registered_id == capability_id
            },
            key=_version_key,
            reverse=True,
        )
        if not versions:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"capability {capability_id!r} is not registered",
            )
        selected_version = version or versions[0]
        if selected_version not in versions:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                (
                    f"capability {capability_id!r} does not provide requested "
                    f"version {selected_version!r}"
                ),
                details={"available_versions": versions},
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


def _version_key(version: str) -> tuple[int, ...]:
    """Sort dotted numeric versions naturally, with a stable fallback."""

    parts = version.split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return (0,)
