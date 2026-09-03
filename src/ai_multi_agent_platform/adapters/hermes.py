"""Optional Hermes Agent orchestration adapter.

Hermes remains an external, replaceable orchestration service.  This module maps
platform-owned planning and Agent/Team snapshots onto Hermes' documented HTTP API
without importing Hermes runtime classes or promoting Hermes IDs into canonical state.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast
from urllib import error, parse, request

from ai_multi_agent_platform.agents.models import AgentExecutionSpec, OrchestratorMapping
from ai_multi_agent_platform.agents.runtime import AgentOrchestratorMapper
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

HERMES_UPSTREAM_REPOSITORY = "https://github.com/NousResearch/hermes-agent"
HERMES_PINNED_REVISION = "63279301bcbdc185c1b07b98a9312eb0c862f26d"
HERMES_ADAPTER_ID = "hermes-api-server"


@dataclass(frozen=True, slots=True)
class HermesHttpResponse:
    status_code: int
    payload: JsonValue


class HermesHttpTransport(Protocol):
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse: ...


class UrllibHermesHttpTransport:
    """Dependency-free JSON transport for a separately deployed Hermes API server."""

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        try:
            return await asyncio.to_thread(
                self._request_json_sync,
                method,
                url,
                payload,
                headers,
                timeout_seconds,
            )
        except TimeoutError:
            raise
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError(str(exc.reason)) from exc
            raise ConnectionError(str(exc.reason)) from exc

    @staticmethod
    def _request_json_sync(
        method: str,
        url: str,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        body = None
        request_headers = {"accept": "application/json", **dict(headers)}
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            request_headers["content-type"] = "application/json"
        http_request = request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                parsed: JsonValue = json.loads(raw) if raw else None
                return HermesHttpResponse(response.status, parsed)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return HermesHttpResponse(exc.code, parsed)


@dataclass(frozen=True, slots=True)
class HermesAdapterConfig:
    """Configuration for the external Hermes API-server adapter.

    Secrets remain outside the configuration object: ``api_key_env`` names an
    environment/secret reference resolved only when a request is emitted.
    """

    enabled: bool = False
    base_url: str = "http://127.0.0.1:8642"
    api_key_env: str = "API_SERVER_KEY"
    pinned_revision: str = HERMES_PINNED_REVISION
    request_timeout_seconds: float = 10.0
    plan_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 0.2
    profile: str | None = None
    model_bridge: Mapping[str, str] = field(default_factory=dict)
    capability_bridge: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("Hermes base_url must not be blank")
        if not self.api_key_env.strip():
            raise ValueError("Hermes api_key_env must not be blank")
        if not self.pinned_revision.strip():
            raise ValueError("Hermes pinned_revision must not be blank")
        if self.request_timeout_seconds <= 0:
            raise ValueError("Hermes request_timeout_seconds must be greater than zero")
        if self.plan_timeout_seconds <= 0:
            raise ValueError("Hermes plan_timeout_seconds must be greater than zero")
        if self.poll_interval_seconds <= 0:
            raise ValueError("Hermes poll_interval_seconds must be greater than zero")
        if self.profile is not None and not self.profile.strip():
            raise ValueError("Hermes profile must not be blank")
        if any(not key.strip() or not value.strip() for key, value in self.model_bridge.items()):
            raise ValueError("Hermes model_bridge keys and values must not be blank")
        if any(not key.strip() or not value.strip() for key, value in self.capability_bridge.items()):
            raise ValueError("Hermes capability_bridge keys and values must not be blank")


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

    @property
    def descriptor(self) -> ProviderDescriptor:
        available = self.config.enabled
        health = HealthStatus.UNKNOWN if available else HealthStatus.UNAVAILABLE
        capability = Capability(
            name="hermes.orchestration",
            kind=CapabilityKind.ORCHESTRATION,
            supported_operations=("plan", "cancel", "reconcile"),
            features=(
                "external-http-service",
                "idempotent-runs",
                "pollable-status",
                "cancellation",
            ),
            attributes={
                "upstream_repository": HERMES_UPSTREAM_REPOSITORY,
                "pinned_revision": self.config.pinned_revision,
            },
            adapter_metadata=_adapter_metadata(
                transport="api-server",
                upstream_revision=self.config.pinned_revision,
            ),
        )
        return ProviderDescriptor(
            provider_id=HERMES_ADAPTER_ID,
            provider_type="orchestrator",
            supported_operations=("plan", "cancel", "reconcile"),
            capabilities=(capability,),
            health=health,
            available=available,
            adapter_metadata=_adapter_metadata(
                transport="api-server",
                upstream_revision=self.config.pinned_revision,
            ),
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
        )
        self._raise_for_status(response, operation="start planning run")
        admitted = self._object(response.payload, "run admission response")
        external_run_id = self._required_string(admitted, "run_id")

        timeout_seconds = (
            request_data.context.control.timeout_seconds or self.config.plan_timeout_seconds
        )
        deadline = time.monotonic() + timeout_seconds
        try:
            snapshot = await self._wait_for_terminal_run(
                external_run_id,
                context=request_data.context,
                deadline=deadline,
            )
        except asyncio.CancelledError:
            await self._stop_best_effort(external_run_id, request_data.context)
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
            snapshot.error or f"Hermes planning run failed with status {snapshot.status}",
            external_run_id=external_run_id,
        )

    async def reconcile_external_run(
        self,
        external_run_id: str,
        context: OperationContext,
    ) -> HermesRunSnapshot:
        """Read one Hermes-native run without promoting it to canonical lifecycle state."""

        self._require_enabled()
        response = await self._request(
            "GET",
            f"/v1/runs/{parse.quote(external_run_id, safe='')}",
            context=context,
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
        response = await self._request(
            "POST",
            f"/v1/runs/{parse.quote(external_run_id, safe='')}/stop",
            payload={},
            context=context,
        )
        self._raise_for_status(response, operation="cancel run")
        return await self.reconcile_external_run(external_run_id, context)

    async def _wait_for_terminal_run(
        self,
        external_run_id: str,
        *,
        context: OperationContext,
        deadline: float,
    ) -> HermesRunSnapshot:
        while True:
            if time.monotonic() >= deadline:
                await self._stop_best_effort(external_run_id, context)
                raise self._provider_error(
                    ErrorCode.TIMEOUT,
                    "Hermes planning run exceeded the canonical timeout",
                    retryable=True,
                    external_run_id=external_run_id,
                )
            snapshot = await self.reconcile_external_run(external_run_id, context)
            if snapshot.status not in {"started", "queued", "running"}:
                return snapshot
            await asyncio.sleep(self.config.poll_interval_seconds)

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
        except Exception:
            return

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, JsonValue] | None = None,
        headers: Mapping[str, str] | None = None,
        context: OperationContext | None,
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
                timeout_seconds=self.config.request_timeout_seconds,
            )
        except TimeoutError as exc:
            raise self._provider_error(
                ErrorCode.TIMEOUT,
                "Hermes API request timed out",
                retryable=True,
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise self._provider_error(
                ErrorCode.UNAVAILABLE,
                f"Hermes API server is unavailable: {exc}",
                retryable=True,
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
                    canonical_task_id=task_id,
                    correlation_id=correlation_id,
                ),
            )
        except ValueError as exc:
            raise self._provider_error(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"Hermes returned an invalid plan graph: {exc}",
                external_run_id=external_run_id,
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
    ) -> ContractError:
        values: dict[str, JsonValue] = {
            "upstream_revision": self.config.pinned_revision,
            "transport": "api-server",
        }
        if external_run_id is not None:
            values["external_run_id"] = external_run_id
        return ContractError(
            code,
            message,
            retryable=retryable,
            provider_id=HERMES_ADAPTER_ID,
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
        detail = self._error_message(response.payload)
        suffix = f": {detail}" if detail else ""
        raise self._provider_error(
            code,
            f"Hermes {operation} failed with HTTP {response.status_code}{suffix}",
            retryable=retryable,
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

    @staticmethod
    def _error_message(payload: JsonValue) -> str | None:
        if isinstance(payload, str):
            return payload or None
        if not isinstance(payload, dict):
            return None
        error_value = payload.get("error")
        if isinstance(error_value, str):
            return error_value
        if isinstance(error_value, dict):
            message = error_value.get("message")
            return message if isinstance(message, str) else None
        detail = payload.get("detail")
        return detail if isinstance(detail, str) else None

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


class HermesAgentMapper(AgentOrchestratorMapper):
    """Map exact canonical Agent/Team revisions into Hermes-private runtime metadata."""

    adapter_id = HERMES_ADAPTER_ID

    def __init__(self, config: HermesAdapterConfig) -> None:
        self.config = config

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        if not self.config.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Hermes orchestrator adapter is disabled",
                provider_id=HERMES_ADAPTER_ID,
            )
        model = self._model_mapping(spec)
        capabilities = self._capability_mappings(spec)
        agent = spec.agent_revision
        role_source = agent.profile.instructions.role
        profile: dict[str, JsonValue] = {
            "canonical_agent_id": agent.agent_id,
            "canonical_revision": agent.revision,
            "name": agent.profile.name,
            "role": agent.profile.role,
            "description": agent.profile.description,
            "instructions": {
                "role_content": role_source.content,
                "role_ref": role_source.ref,
                "role_version": role_source.version,
                "platform_constraint_refs": list(
                    agent.profile.instructions.platform_constraint_refs
                ),
                "project_instruction_refs": list(
                    agent.profile.instructions.project_instruction_refs
                ),
            },
            "model": model,
            "capabilities": capabilities,
            "data_access": {
                "memory_scopes": [scope.value for scope in agent.profile.data_access.memory_scopes],
                "memory_config_refs": list(agent.profile.data_access.memory_config_refs),
                "knowledge_source_ids": list(agent.profile.data_access.knowledge_source_ids),
                "allow_user_memory": agent.profile.data_access.allow_user_memory,
            },
            "policy": {
                "authorization_profile_ref": (agent.profile.policy_hooks.authorization_profile_ref),
                "verification_policy_refs": list(
                    agent.profile.policy_hooks.verification_policy_refs
                ),
            },
            "task_context": dict(spec.task_context),
            "project_context": dict(spec.project_context),
        }
        metadata: dict[str, JsonValue] = {
            "mapping_kind": "hermes.api-server.agent/v1",
            "upstream_revision": self.config.pinned_revision,
            "agent": profile,
        }
        if spec.team_revision is not None:
            team = spec.team_revision
            metadata["team"] = {
                "canonical_team_id": team.team_id,
                "canonical_revision": team.revision,
                "name": team.profile.name,
                "coordination_policy_ref": team.profile.coordination_policy_ref,
                "leader_agent_id": team.profile.leader_agent_id,
                "members": [
                    {
                        "agent_id": member.agent.agent_id,
                        "revision": member.agent.revision,
                        "role": member.role,
                        "required": member.required,
                        "can_delegate_to": list(member.can_delegate_to),
                    }
                    for member in team.profile.members
                ],
                "shared_capability_ids": list(team.profile.shared_capability_ids),
                "shared_resource_refs": list(team.profile.shared_resource_refs),
                "max_parallel_agents": team.profile.max_parallel_agents,
                "max_steps": team.profile.max_steps,
                "unavailable_member_policy": (team.profile.unavailable_member_policy.value),
            }
        runtime_ref = f"hermes:mapping:{agent.agent_id}:r{agent.revision}:{spec.run_id}"
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=runtime_ref,
            metadata=metadata,
        )

    def _model_mapping(self, spec: AgentExecutionSpec) -> dict[str, JsonValue]:
        canonical_id = spec.selected_model_config_id
        if canonical_id is None:
            return {
                "canonical_model_config_id": None,
                "provider_id": spec.selected_provider_id,
                "hermes_model": None,
            }
        hermes_model = self.config.model_bridge.get(canonical_id)
        if hermes_model is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Hermes model bridge has no target for the selected canonical model",
                provider_id=HERMES_ADAPTER_ID,
                details={"model_config_id": canonical_id},
            )
        return {
            "canonical_model_config_id": canonical_id,
            "provider_id": spec.selected_provider_id,
            "hermes_model": hermes_model,
        }

    def _capability_mappings(
        self,
        spec: AgentExecutionSpec,
    ) -> list[JsonValue]:
        result: list[JsonValue] = []
        for capability_id in spec.capability_ids:
            hermes_tool = self.config.capability_bridge.get(capability_id)
            if hermes_tool is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Hermes capability bridge has no target for a canonical capability",
                    provider_id=HERMES_ADAPTER_ID,
                    details={"capability_id": capability_id},
                )
            result.append(
                {
                    "canonical_capability_id": capability_id,
                    "canonical_version": spec.capability_versions.get(capability_id),
                    "hermes_tool": hermes_tool,
                }
            )
        return result
