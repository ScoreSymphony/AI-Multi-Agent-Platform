"""Provider-health aggregation for the Control Plane."""

from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability.health import ReadinessState, ServiceHealth


class ControlPlaneHealth:
    """Aggregate provider readiness without owning provider lifecycle."""

    def __init__(
        self,
        providers: tuple[ProviderContract, ...],
        *,
        probe_timeout_seconds: float = 10.0,
    ) -> None:
        if probe_timeout_seconds <= 0:
            raise ValueError("probe_timeout_seconds must be positive")
        self._providers = providers
        self._probe_timeout_seconds = probe_timeout_seconds

    async def health(self) -> dict[str, JsonValue]:
        providers: list[JsonValue] = []
        states: list[ReadinessState] = []
        ready = True
        for provider in self._providers:
            descriptor = provider.descriptor
            probe_error: str | None = None
            probe_succeeded = False
            provider_timeout = getattr(provider, "health_timeout_seconds", None)
            timeout_seconds = (
                float(provider_timeout)
                if isinstance(provider_timeout, (int, float)) and provider_timeout > 0
                else self._probe_timeout_seconds
            )
            try:
                status = await asyncio.wait_for(
                    provider.health(),
                    timeout=timeout_seconds,
                )
                probe_succeeded = True
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                status = descriptor.health
                probe_error = ErrorCode.TIMEOUT.value
            except ContractError as exc:
                status = descriptor.health
                probe_error = exc.code.value
            # error-boundary: allow-broad-catch=boundary northbound health normalization
            except Exception:
                status = descriptor.health
                probe_error = ErrorCode.BACKEND_ERROR.value

            service_health = getattr(provider, "service_health", None) if probe_succeeded else None
            if isinstance(service_health, ServiceHealth):
                provider_state = service_health.readiness
                provider_ready = service_health.ready and descriptor.available
                dependencies: list[JsonValue] = [
                    {
                        "name": dependency.name,
                        "state": dependency.state.value,
                        "required": dependency.required,
                        "detail": dependency.detail,
                        "error_code": dependency.error_code,
                        "attempts": dependency.attempts,
                        "failure_count": dependency.failure_count,
                        "recovery_count": dependency.recovery_count,
                        "operator_action": dependency.operator_action,
                    }
                    for dependency in service_health.dependencies
                ]
            else:
                provider_ready = (
                    probe_succeeded
                    and descriptor.available
                    and status.value != "unavailable"
                )
                if not probe_succeeded or not descriptor.available or status.value == "unavailable":
                    provider_state = ReadinessState.UNAVAILABLE
                elif status.value in {"degraded", "unknown"}:
                    provider_state = ReadinessState.DEGRADED
                else:
                    provider_state = ReadinessState.READY
                dependencies = []

            if not provider_ready:
                ready = False
            states.append(provider_state)
            provider_payload: dict[str, JsonValue] = {
                "id": descriptor.provider_id,
                "type": descriptor.provider_type,
                "status": status.value if probe_succeeded else "unavailable",
                "available": descriptor.available,
                "readiness_state": provider_state.value,
            }
            if dependencies:
                provider_payload["dependencies"] = dependencies
            if probe_error is not None:
                provider_payload["error_code"] = probe_error
                provider_payload["operator_action"] = (
                    "restore the required health dependency and rerun platform doctor"
                )
            providers.append(provider_payload)

        readiness_state = _aggregate_readiness(states)
        if readiness_state not in {ReadinessState.READY, ReadinessState.DEGRADED}:
            ready = False
        return {
            "status": "healthy",
            "alive": True,
            "ready": ready,
            "readiness_state": readiness_state.value,
            "api_version": "v1",
            "providers": providers,
        }


def _aggregate_readiness(states: list[ReadinessState]) -> ReadinessState:
    for candidate in (
        ReadinessState.OPERATOR_INTERVENTION_REQUIRED,
        ReadinessState.RECONCILING,
        ReadinessState.DRAINING,
        ReadinessState.UNAVAILABLE,
        ReadinessState.DEGRADED,
    ):
        if candidate in states:
            return candidate
    return ReadinessState.READY
