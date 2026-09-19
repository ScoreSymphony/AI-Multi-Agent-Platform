"""Repository-specific fallback after runtime provider degradation.

CapabilityRegistry already falls back when an optional provider is unavailable at resolution time.
Repository indexes can become stale only after a repository-specific request is inspected, though.
This coordinator handles that second case without bypassing #12: both the preferred attempt and the
baseline fallback are ordinary capability-invocation calls with the same policy/governance context.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from ai_multi_agent_platform.capabilities import CapabilityInvocation, CapabilityInvocationResult
from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .capabilities import RepositoryIntelligenceOperation

_BASELINE_FALLBACK_CAPABILITIES = frozenset(
    {
        RepositoryIntelligenceOperation.MAP.value,
        RepositoryIntelligenceOperation.TEXT_SEARCH.value,
        RepositoryIntelligenceOperation.SOURCE_SLICE.value,
    }
)
_DEFAULT_FALLBACK_ERRORS = frozenset({ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT})


class CapabilityInvocationPort(Protocol):
    """Small structural seam shared by canonical and repository-specific invokers."""

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult: ...


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceInvocationOutcome:
    result: CapabilityInvocationResult
    fallback_used: bool = False
    preferred_error_code: str | None = None
    preferred_provider_id: str | None = None


class RepositoryIntelligenceFallbackInvoker:
    """Invoke preferred intelligence, then the canonical baseline only when policy allows it."""

    def __init__(
        self,
        preferred: CapabilityInvocationPort,
        baseline: CapabilityInvocationPort,
        *,
        fallback_error_codes: frozenset[ErrorCode] = _DEFAULT_FALLBACK_ERRORS,
    ) -> None:
        self._preferred = preferred
        self._baseline = baseline
        self._fallback_error_codes = fallback_error_codes

    async def invoke(
        self,
        request: CapabilityInvocation,
        *,
        provider_specific_required: bool = False,
    ) -> RepositoryIntelligenceInvocationOutcome:
        try:
            result = await self._preferred.invoke(request)
            return RepositoryIntelligenceInvocationOutcome(result=result)
        except ContractError as exc:
            if provider_specific_required:
                raise
            if request.capability_id not in _BASELINE_FALLBACK_CAPABILITIES:
                raise
            if exc.code not in self._fallback_error_codes:
                raise
            fallback_request = replace(
                request,
                invocation_id=f"{request.invocation_id}:baseline-fallback",
            )
            fallback = await self._baseline.invoke(fallback_request)
            return RepositoryIntelligenceInvocationOutcome(
                result=fallback,
                fallback_used=True,
                preferred_error_code=exc.code.value,
                preferred_provider_id=exc.provider_id,
            )


class RepositoryIntelligenceFallbackResultPort:
    """Plain invocation port for consumers that only need the selected/fallback result.

    Higher-level evaluation or telemetry code can keep using
    ``RepositoryIntelligenceFallbackInvoker`` directly when it needs explicit fallback evidence.
    Context/Search consumers can depend on this
    narrower port and remain unaware of provider selection details.
    """

    def __init__(
        self,
        coordinator: RepositoryIntelligenceFallbackInvoker,
        *,
        provider_specific_required: bool = False,
    ) -> None:
        self._coordinator = coordinator
        self._provider_specific_required = provider_specific_required

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        outcome = await self._coordinator.invoke(
            request,
            provider_specific_required=self._provider_specific_required,
        )
        return outcome.result


__all__ = [
    "CapabilityInvocationPort",
    "RepositoryIntelligenceFallbackInvoker",
    "RepositoryIntelligenceFallbackResultPort",
    "RepositoryIntelligenceInvocationOutcome",
]
