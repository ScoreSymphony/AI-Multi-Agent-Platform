"""Optional Hermes Agent orchestration adapter.

Hermes remains an external, replaceable orchestration service. This stable public
facade exposes the platform-owned Hermes adapter surface while focused sibling
modules own configuration policy, HTTP transport and Agent/Team mapping.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast
from urllib import parse

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import Orchestrator
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
    ProviderDescriptor,
)

from .hermes_config import (
    HERMES_ADAPTER_ID,
    HERMES_CONFIGURATION_SCHEMA,
    HERMES_PINNED_REVISION,
    HERMES_UPSTREAM_REPOSITORY,
    HermesAdapterConfig,
    HermesBridgeMode,
    HermesCompatibilityStatus,
    HermesDiagnosticsMode,
    HermesRetryBehavior,
    HermesRuntimeMode,
)
from .hermes_http import HermesHttpResponse, HermesHttpTransport, UrllibHermesHttpTransport
from .hermes_mapping import HermesAgentMapper


@dataclass(frozen=True, slots=True)
class HermesRunSnapshot:
    """Adapter-private view of one Hermes-native run."""

    external_run_id: str
    status: str
    output: str | None = None
    error: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.external_run_id.strip():
            raise ValueError("Hermes external_run_id must not be blank")
        if not self.status.strip():
            raise ValueError("Hermes run status must not be blank")


def _adapter_metadata(**values: JsonValue) -> tuple[AdapterMetadata, ...]:
    return (AdapterMetadata(namespace="hermes", values=dict(values)),)


class HermesOrchestrator(Orchestrator):
    """Canonical ``Orchestrator`` backed by Hermes' documented ``/v1/runs`` API."""

    def __init__(
        self,
        config: HermesAdapterConfig,
        *,
        transport: HermesHttpTransport | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibHermesHttpTransport()
        self.secret_resolver = secret_resolver or os.getenv
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    @property
    def descriptor(self) -> ProviderDescriptor:
        available = self.config.enabled
        health = HealthStatus.UNKNOWN if available else HealthStatus.UNAVAILABLE
        metadata = _adapter_metadata(
            transport=self.config.runtime_mode.value,
            upstream_revision=self.config.pinned_revision,
            retry_behavior=self.config.retry_behavior.value,
            bridge_mode=self.config.bridge_mode.value,
            diagnostics_mode=self.config.diagnostics_mode.value,
            compatibility_status=self.config.compatibility_status.value,
        )
        capability = Capability(
            name="hermes.orchestration",
            kind=CapabilityKind.ORCHESTRATION,
            supported_operations=("plan", "cancel", "reconcile"),
            features=(
                "external-http-service",
                "idempotent-runs",
                "pollable-status",
                "cancellation",
                "strict-model-capability-bridge",
                "platform-owned-retry-policy",
            ),
            attributes={
                "upstream_repository": HERMES_UPSTREAM_REPOSITORY,
                "pinned_revision": self.config.pinned_revision,
                "compatibility_status": self.config.compatibility_status.value,
            },
            adapter_metadata=metadata,
        )
        return ProviderDescriptor(
            provider_id=HERMES_ADAPTER_ID,
            provider_type="orchestrator",
            supported_operations=("plan", "cancel", "reconcile"),
            capabilities=(capability,),
            health=health,
            available=available,
            adapter_metadata=metadata,
        )

    async def health(self) -> HealthStatus:
        if not self.config.enabled:
            return HealthStatus.UNAVAILABLE
        try:
            response = await self._request("GET", "/health", context=None)
        except ContractError:
            return HealthStatus.UNAVAILABLE
        if 200 <= response.status_code < 300:
            return HealthStatus.HEALTHY
        return HealthStatus.DEGRADED if response.status_code < 500 else HealthStatus.UNAVAILABLE

    async def plan(self, request_data: PlanRequest) -> PlanResponse:
        self._require_enabled()
        timeout_seconds = (
            request_data.context.control.timeout_seconds or self.config.plan_timeout_seconds
        )
        deadline = time.monotonic() + timeout_seconds
        external_run_id: str | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                payload: dict[str, JsonValue] = {
                    "input": self._planning_input(request_data),
                    "instructions": self._planning_instructions(),
                }
                headers = self._headers(
                    request_data.context,
                    idempotency_key=self._idempotency_key(request_data),
                )
                response = await self._request(
                    "POST",
                    "/v1/runs",
                    payload=payload,
                    headers=headers,
                    context=request_data.context,
                    timeout_seconds=timeout_seconds,
                )
                self._raise_for_status(response, operation="start planning run")
                admitted = self._object(response.payload, "run admission response")
                external_run_id = self._required_string(admitted, "run_id")
                snapshot = await self._wait_for_terminal_run(
                    external_run_id,
                    context=request_data.context,
                    deadline=deadline,
                    timeout_ceiling=timeout_seconds,
                )
        except TimeoutError as exc:
            if external_run_id is not None:
                self._schedule_stop_best_effort(external_run_id, request_data.context)
            raise self._provider_error(
                ErrorCode.TIMEOUT,
                "Hermes planning run exceeded the canonical provider-boundary timeout",
                retryable=True,
                external_run_id=external_run_id,
            ) from exc
        except asyncio.CancelledError:
            if external_run_id is not None:
                self._schedule_stop_best_effort(external_run_id, request_data.context)
            raise
        if snapshot.status == "completed":
            if snapshot.output is None:
                raise self._provider_error(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "Hermes completed a planning run without an output",
                    external_run_id=external_run_id,
                )
            return self._parse_plan(
                snapshot.output,
                external_run_id=external_run_id,
                task_id=request_data.task_id,
                correlation_id=request_data.context.correlation_id,
            )
        if snapshot.status in {"cancelled", "interrupted"}:
            raise self._provider_error(
                ErrorCode.CANCELLED,
                f"Hermes planning run ended as {snapshot.status}",
                external_run_id=external_run_id,
            )
        if snapshot.status == "waiting_for_approval":
            raise self._provider_error(
                ErrorCode.FORBIDDEN,
                "Hermes planning run requires approval; the canonical planning contract "
                "does not delegate approval authority to the orchestrator",
                external_run_id=external_run_id,
            )
        raise self._provider_error(
            ErrorCode.BACKEND_ERROR,
            f"Hermes planning run failed with status {snapshot.status}",
            external_run_id=external_run_id,
            details={"provider_status": snapshot.status},
        )

    async def reconcile_external_run(
        self,
        external_run_id: str,
        context: OperationContext,
    ) -> HermesRunSnapshot:
        """Read one Hermes-native run without promoting it to canonical lifecycle state."""

        self._require_enabled()
        timeout_seconds = context.control.timeout_seconds
        try:
            async with asyncio.timeout(timeout_seconds):
                return await self._reconcile_external_run(
                    external_run_id,
                    context,
                    timeout_seconds=timeout_seconds,
                )
        except TimeoutError as exc:
            raise self._provider_error(
                ErrorCode.TIMEOUT,
                "Hermes reconciliation exceeded the canonical provider-boundary timeout",
                retryable=True,
                external_run_id=external_run_id,
            ) from exc

    async def _reconcile_external_run(
        self,
        external_run_id: str,
        context: OperationContext,
        *,
        timeout_seconds: float | None,
    ) -> HermesRunSnapshot:
        response = await self._request(
            "GET",
            f"/v1/runs/{parse.quote(external_run_id, safe='')}",
            context=context,
            timeout_seconds=timeout_seconds,
        )
        self._raise_for_status(response, operation="reconcile run")
        return self._snapshot(self._object(response.payload, "run status response"))

    async def cancel_external_run(
        self,
        external_run_id: str,
        context: OperationContext,
    ) -> HermesRunSnapshot:
        """Request Hermes cancellation and then return its adapter-private status."""

        self._require_enabled()
        timeout_seconds = context.control.timeout_seconds
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._request(
                    "POST",
                    f"/v1/runs/{parse.quote(external_run_id, safe='')}/stop",
                    payload={},
                    context=context,
                    timeout_seconds=timeout_seconds,
                )
                self._raise_for_status(response, operation="cancel run")
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError
                return await self._reconcile_external_run(
                    external_run_id,
                    context,
                    timeout_seconds=remaining,
                )
        except TimeoutError as exc:
            raise self._provider_error(
                ErrorCode.TIMEOUT,
                "Hermes cancellation exceeded the canonical provider-boundary timeout",
                retryable=True,
                external_run_id=external_run_id,
            ) from exc

    async def _wait_for_terminal_run(
        self,
        external_run_id: str,
        *,
        context: OperationContext,
        deadline: float,
        timeout_ceiling: float,
    ) -> HermesRunSnapshot:
        while True:
            remaining = min(timeout_ceiling, deadline - time.monotonic())
            if remaining <= 0:
                self._schedule_stop_best_effort(external_run_id, context)
                raise self._provider_error(
                    ErrorCode.TIMEOUT,
                    "Hermes planning run exceeded the canonical timeout",
                    retryable=True,
                    external_run_id=external_run_id,
                )
            snapshot = await self._reconcile_external_run(
                external_run_id,
                context,
                timeout_seconds=remaining,
            )
            if snapshot.status not in {"started", "queued", "running"}:
                return snapshot
            remaining = min(timeout_ceiling, deadline - time.monotonic())
            if remaining <= 0:
                continue
            await asyncio.sleep(min(self.config.poll_interval_seconds, remaining))

    def _schedule_stop_best_effort(
        self,
        external_run_id: str,
        context: OperationContext,
    ) -> None:
        task = asyncio.create_task(self._stop_best_effort(external_run_id, context))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _stop_best_effort(
        self,
        external_run_id: str,
        context: OperationContext,
    ) -> None:
        try:
            await asyncio.shield(
                self._request(
                    "POST",
                    f"/v1/runs/{parse.quote(external_run_id, safe='')}/stop",
                    payload={},
                    context=context,
                )
            )
        except ContractError:
            return

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, JsonValue] | None = None,
        headers: Mapping[str, str] | None = None,
        context: OperationContext | None,
        timeout_seconds: float | None = None,
    ) -> HermesHttpResponse:
        request_headers = self._headers(context)
        if headers:
            request_headers.update(headers)
        try:
            return await self.transport.request_json(
                method,
                f"{self._base_url}{path}",
                payload=payload,
                headers=request_headers,
                timeout_seconds=(
                    self.config.request_timeout_seconds
                    if timeout_seconds is None
                    else min(self.config.request_timeout_seconds, timeout_seconds)
                ),
            )
        except TimeoutError as exc:
            raise self._provider_error(
                ErrorCode.TIMEOUT,
                "Hermes API request timed out",
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise self._provider_error(
                ErrorCode.UNAVAILABLE,
                "Hermes API server is unavailable",
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc
        except ContractError:
            raise
        # error-boundary: allow-broad-catch=translation external Hermes transport boundary
        except Exception as exc:
            raise self._provider_error(
                ErrorCode.BACKEND_ERROR,
                "Hermes API transport failed",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            ) from exc

    @property
    def _base_url(self) -> str:
        base = self.config.base_url.rstrip("/")
        if self.config.profile is None:
            return base
        return f"{base}/p/{parse.quote(self.config.profile, safe='')}"

    def _headers(
        self,
        context: OperationContext | None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_key = self.secret_resolver(self.config.api_key_env)
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if context is not None:
            headers["X-Correlation-Id"] = context.correlation_id
        return headers

    def _idempotency_key(self, request_data: PlanRequest) -> str:
        configured = request_data.context.control.idempotency_key
        if configured:
            return configured
        raw = f"plan:{request_data.task_id}:{request_data.context.correlation_id}"
        return raw[:255]

    @staticmethod
    def _planning_instructions() -> str:
        return (
            "Act only as an orchestration planner. Do not execute the task. "
            "Return only one JSON object with keys 'summary' and 'steps'. "
            "Each step must contain 'key', 'title', optional 'objective', and "
            "optional 'depends_on' (an array of step keys). Do not invent canonical "
            "Plan or Step IDs; proposal-local step keys are sufficient."
        )

    @staticmethod
    def _planning_input(request_data: PlanRequest) -> str:
        return (
            f"Canonical task: {request_data.task_id}\n"
            f"Correlation: {request_data.context.correlation_id}\n"
            f"Objective:\n{request_data.objective}\n\n"
            "Produce a concise dependency-aware plan proposal."
        )

    def _parse_plan(
        self,
        raw: str,
        *,
        external_run_id: str,
        task_id: str,
        correlation_id: str,
    ) -> PlanResponse:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise self._provider_error(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "Hermes planning output is not valid JSON",
                external_run_id=external_run_id,
            ) from exc
        data = self._object(cast(JsonValue, payload), "planning output")
        summary = self._required_string(data, "summary")
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list):
            raise self._provider_error(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "Hermes planning output field 'steps' must be an array",
                external_run_id=external_run_id,
            )
        steps: list[PlanStepProposal] = []
        for index, item in enumerate(raw_steps):
            if not isinstance(item, dict):
                raise self._provider_error(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    f"Hermes planning step {index} must be an object",
                    external_run_id=external_run_id,
                )
            key = self._required_string(item, "key")
            title = self._required_string(item, "title")
            objective = item.get("objective", "")
            if not isinstance(objective, str):
                raise self._provider_error(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    f"Hermes planning step {index} objective must be a string",
                    external_run_id=external_run_id,
                )
            depends_on_raw = item.get("depends_on", [])
            if not isinstance(depends_on_raw, list) or not all(
                isinstance(value, str) and value.strip() for value in depends_on_raw
            ):
                raise self._provider_error(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    f"Hermes planning step {index} depends_on must be an array of strings",
                    external_run_id=external_run_id,
                )
            steps.append(
                PlanStepProposal(
                    key=key,
                    title=title,
                    objective=objective,
                    depends_on=tuple(cast(list[str], depends_on_raw)),
                )
            )
        try:
            return PlanResponse(
                summary=summary,
                steps=tuple(steps),
                adapter_metadata=_adapter_metadata(
                    external_run_id=external_run_id,
                    upstream_revision=self.config.pinned_revision,
                    compatibility_status=self.config.compatibility_status.value,
                    canonical_task_id=task_id,
                    correlation_id=correlation_id,
                ),
            )
        except ValueError as exc:
            raise self._provider_error(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "Hermes returned an invalid plan graph",
                external_run_id=external_run_id,
                details={"exception_type": type(exc).__name__},
            ) from exc

    def _snapshot(self, payload: Mapping[str, JsonValue]) -> HermesRunSnapshot:
        external_run_id = self._required_string(payload, "run_id")
        status = self._required_string(payload, "status")
        output = payload.get("output")
        error_message = payload.get("error")
        session_id = payload.get("session_id")
        return HermesRunSnapshot(
            external_run_id=external_run_id,
            status=status,
            output=output if isinstance(output, str) else None,
            error=error_message if isinstance(error_message, str) else None,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise self._provider_error(
                ErrorCode.UNAVAILABLE,
                "Hermes orchestrator adapter is disabled",
            )

    def _provider_error(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        external_run_id: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> ContractError:
        values: dict[str, JsonValue] = {
            "upstream_revision": self.config.pinned_revision,
            "compatibility_status": self.config.compatibility_status.value,
            "transport": self.config.runtime_mode.value,
        }
        if external_run_id is not None:
            values["external_run_id"] = external_run_id
        return ContractError(
            code,
            message,
            retryable=retryable,
            provider_id=HERMES_ADAPTER_ID,
            details=details,
            adapter_metadata=(AdapterMetadata(namespace="hermes", values=values),),
        )

    def _raise_for_status(
        self,
        response: HermesHttpResponse,
        *,
        operation: str,
    ) -> None:
        if 200 <= response.status_code < 300:
            return
        code, retryable = self._http_error(response.status_code)
        raise self._provider_error(
            code,
            f"Hermes {operation} failed with HTTP {response.status_code}",
            retryable=retryable,
            details={"http_status": response.status_code},
        )

    @staticmethod
    def _http_error(status_code: int) -> tuple[ErrorCode, bool]:
        if status_code == 400:
            return ErrorCode.INVALID_REQUEST, False
        if status_code == 401:
            return ErrorCode.UNAUTHORIZED, False
        if status_code == 403:
            return ErrorCode.FORBIDDEN, False
        if status_code == 404:
            return ErrorCode.NOT_FOUND, False
        if status_code == 409:
            return ErrorCode.CONFLICT, False
        if status_code == 429:
            return ErrorCode.RATE_LIMITED, True
        if status_code >= 500:
            return ErrorCode.UNAVAILABLE, True
        return ErrorCode.BACKEND_ERROR, False

    def _object(self, value: JsonValue, label: str) -> dict[str, JsonValue]:
        if not isinstance(value, dict):
            raise self._provider_error(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"Hermes {label} must be a JSON object",
            )
        return value

    def _required_string(
        self,
        payload: Mapping[str, JsonValue],
        key: str,
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise self._provider_error(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"Hermes response is missing non-blank '{key}'",
            )
        return value
