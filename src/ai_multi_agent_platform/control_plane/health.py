"""Provider-health aggregation for the Control Plane."""

from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue
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
            payload, state, provider_ready = await self._provider_health(provider)
            providers.append(payload)
            states.append(state)
            ready = ready and provider_ready

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

    async def _provider_health(
        self,
        provider: ProviderContract,
    ) -> tuple[JsonValue, ReadinessState, bool]:
        descriptor = provider.descriptor
        status, probe_succeeded, probe_error = await self._probe_status(provider)
        service_health = getattr(provider, "service_health", None) if probe_succeeded else None

        if isinstance(service_health, ServiceHealth):
            provider_state = service_health.readiness
            provider_ready = service_health.ready and descriptor.available
            dependencies = self._dependency_payloads(service_health)
        else:
            provider_state = self._plain_provider_state(
                status,
                probe_succeeded=probe_succeeded,
                available=descriptor.available,
            )
            provider_ready = (
                probe_succeeded
                and descriptor.available
                and status is not HealthStatus.UNAVAILABLE
            )
            dependencies = []

        payload: dict[str, JsonValue] = {
            "id": descriptor.provider_id,
            "type": descriptor.provider_type,
            "status": status.value if probe_succeeded else "unavailable",
            "available": descriptor.available,
            "readiness_state": provider_state.value,
        }
        diagnostics = getattr(provider, "health_diagnostics", ())
        if diagnostics:
            payload["diagnostics"] = list(diagnostics)
        if dependencies:
            payload["dependencies"] = dependencies
        if probe_error is not None:
            payload["error_code"] = probe_error
            payload["operator_action"] = (
                "restore the required health dependency and rerun platform doctor"
            )
        return payload, provider_state, provider_ready

    async def _probe_status(
        self,
        provider: ProviderContract,
    ) -> tuple[HealthStatus, bool, str | None]:
        descriptor = provider.descriptor
        timeout_seconds = self._provider_timeout(provider)
        try:
            status = await asyncio.wait_for(
                provider.health(),
                timeout=timeout_seconds,
            )
            return status, True, None
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return descriptor.health, False, ErrorCode.TIMEOUT.value
        except ContractError as exc:
            return descriptor.health, False, exc.code.value
        # error-boundary: allow-broad-catch=boundary northbound health normalization
        except Exception:
            return descriptor.health, False, ErrorCode.BACKEND_ERROR.value

    def _provider_timeout(self, provider: ProviderContract) -> float:
        provider_timeout = getattr(provider, "health_timeout_seconds", None)
        if isinstance(provider_timeout, (int, float)) and provider_timeout > 0:
            return float(provider_timeout)
        return self._probe_timeout_seconds

    @staticmethod
    def _plain_provider_state(
        status: HealthStatus,
        *,
        probe_succeeded: bool,
        available: bool,
    ) -> ReadinessState:
        if not probe_succeeded or not available or status is HealthStatus.UNAVAILABLE:
            return ReadinessState.UNAVAILABLE
        if status in {HealthStatus.DEGRADED, HealthStatus.UNKNOWN}:
            return ReadinessState.DEGRADED
        return ReadinessState.READY

    @staticmethod
    def _dependency_payloads(service_health: ServiceHealth) -> list[JsonValue]:
        return [
            {
                "name": dependency.name,
                "state": dependency.state.value,
                "required": dependency.required,
                "detail": dependency.detail,
                "error_code": dependency.error_code,
                "attempts": dependency.attempts,
                "retry_count": dependency.retry_count,
                "last_retry_error_code": dependency.last_retry_error_code,
                "probe_duration_seconds": dependency.probe_duration_seconds,
                "degraded_duration_seconds": dependency.degraded_duration_seconds,
                "failure_count": dependency.failure_count,
                "recovery_count": dependency.recovery_count,
                "operator_action": dependency.operator_action,
            }
            for dependency in service_health.dependencies
        ]


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
