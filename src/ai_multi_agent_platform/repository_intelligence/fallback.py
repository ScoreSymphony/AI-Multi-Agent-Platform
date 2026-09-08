"""Repository-specific fallback after runtime provider degradation.

CapabilityRegistry already falls back when an optional provider is unavailable at resolution time.
Repository indexes can become stale only after a repository-specific request is inspected, though.
This coordinator handles that second case without bypassing #12: both the preferred attempt and the
baseline fallback are ordinary CapabilityInvoker calls with the same policy/governance context.
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


__all__ = [
    "CapabilityInvocationPort",
    "RepositoryIntelligenceFallbackInvoker",
    "RepositoryIntelligenceInvocationOutcome",
]
