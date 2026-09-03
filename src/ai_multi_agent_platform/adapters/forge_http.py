"""HTTP transport for the optional Forge executor sidecar."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast
from urllib import error, parse, request

from ai_multi_agent_platform.contracts.types import JsonValue

from .forge import (
    ForgeArtifact,
    ForgeClientRequest,
    ForgeClientResult,
    ForgeExecutionStatus,
    ForgeHealth,
)


@dataclass(frozen=True, slots=True)
class ForgeHttpResponse:
    status_code: int
    payload: JsonValue


class ForgeHttpTransport(Protocol):
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> ForgeHttpResponse: ...


class UrllibForgeHttpTransport:
    """Dependency-free HTTP transport intended for a local Forge sidecar."""

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> ForgeHttpResponse:
        return await asyncio.to_thread(
            self._request_json_sync,
            method,
            url,
            payload,
            timeout_seconds,
        )

    @staticmethod
    def _request_json_sync(
        method: str,
        url: str,
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> ForgeHttpResponse:
        body = None
        headers: dict[str, str] = {"accept": "application/json"}
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            headers["content-type"] = "application/json"
        http_request = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                parsed: JsonValue = json.loads(raw) if raw else None
                return ForgeHttpResponse(response.status, parsed)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return ForgeHttpResponse(exc.code, parsed)


@dataclass(frozen=True, slots=True)
class ForgeHttpClientConfig:
    base_url: str
    executor_type: str
    executor_config: Mapping[str, JsonValue] = field(default_factory=dict)
    poll_interval_seconds: float = 0.05
    request_timeout_seconds: float = 5.0
    heartbeat_interval_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url must not be blank")
        if not self.executor_type.strip():
            raise ValueError("executor_type must not be blank")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be greater than zero")


class ForgeHttpClient:
    """Concrete ``ForgeClient`` for the execution-only Rust sidecar."""

    def __init__(
        self,
        config: ForgeHttpClientConfig,
        *,
        transport: ForgeHttpTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibForgeHttpTransport()
        self._base_url = config.base_url.rstrip("/")

    async def health(self) -> ForgeHealth:
        response = await self._request("GET", "/healthz")
        self._raise_for_status(response, operation="health")
        payload = self._object(response.payload, "health response")
        allowed = self._strings(payload.get("allowed_executor_types"))
        configured_status = self._configured_executor_status(payload.get("executors"))
        healthy = payload.get("healthy") is True and self.config.executor_type in allowed
        if configured_status == "not_found":
            healthy = False
        metadata: dict[str, JsonValue] = {
            "transport": "http-sidecar",
            "base_url": self._base_url,
            "executor_type": self.config.executor_type,
        }
        protocol_version = payload.get("protocol_version")
        if isinstance(protocol_version, str):
            metadata["protocol_version"] = protocol_version
        if configured_status is not None:
            metadata["executor_status"] = configured_status
        return ForgeHealth(healthy=healthy, capabilities=("execute",), metadata=metadata)

    async def execute(self, request_data: ForgeClientRequest) -> ForgeClientResult:
        payload: dict[str, JsonValue] = {
            "request_ref": request_data.request_ref,
            "task_id": request_data.task_id,
            "run_id": request_data.run_id,
            "step_id": request_data.step_id,
            "correlation_id": request_data.correlation_id,
            "workspace_path": request_data.workspace_path,
            "description": self._description(request_data),
            "executor_type": self.config.executor_type,
            "config": dict(self.config.executor_config),
            "timeout_seconds": request_data.timeout_seconds,
            "max_turns": self._optional_positive_int(request_data.policy_context.get("max_turns")),
            "heartbeat_interval_seconds": self.config.heartbeat_interval_seconds,
        }
        response = await self._request("POST", "/v1/executions", payload=payload)
        self._raise_for_status(response, operation="submit execution")
        snapshot = self._object(response.payload, "execution submit response")
        while self._status(snapshot) == "running":
            execution_id = self._required_string(snapshot, "execution_id")
            await asyncio.sleep(self.config.poll_interval_seconds)
            response = await self._request(
                "GET",
                f"/v1/executions/{parse.quote(execution_id, safe='')}",
            )
            self._raise_for_status(response, operation="get execution")
            snapshot = self._object(response.payload, "execution status response")
        return self._result(snapshot, request_data)

    async def cancel(self, request_ref: str) -> None:
        response = await self._request(
            "GET",
            f"/v1/requests/{parse.quote(request_ref, safe='')}",
        )
        if response.status_code == 404:
            return
        self._raise_for_status(response, operation="resolve execution for cancellation")
        snapshot = self._object(response.payload, "request lookup response")
        execution_id = self._required_string(snapshot, "execution_id")
        response = await self._request(
            "POST",
            f"/v1/executions/{parse.quote(execution_id, safe='')}/cancel",
            payload={},
        )
        self._raise_for_status(response, operation="cancel execution")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> ForgeHttpResponse:
        return await self.transport.request_json(
            method,
            f"{self._base_url}{path}",
            payload=payload,
            timeout_seconds=self.config.request_timeout_seconds,
        )

    def _configured_executor_status(self, value: JsonValue | None) -> str | None:
        if not isinstance(value, list):
            return None
        for item in value:
            if not isinstance(item, dict) or item.get("executor_type") != self.config.executor_type:
                continue
            status = item.get("status")
            return status if isinstance(status, str) else None
        return None

    @staticmethod
    def _raise_for_status(response: ForgeHttpResponse, *, operation: str) -> None:
        if 200 <= response.status_code < 300:
            return
        message = f"Forge sidecar {operation} failed with HTTP {response.status_code}"
        if isinstance(response.payload, dict):
            error_message = response.payload.get("error")
            if isinstance(error_message, str) and error_message:
                message = f"{message}: {error_message}"
        raise RuntimeError(message)

    @staticmethod
    def _object(value: JsonValue, label: str) -> dict[str, JsonValue]:
        if not isinstance(value, dict):
            raise RuntimeError(f"Forge sidecar {label} must be a JSON object")
        return cast(dict[str, JsonValue], value)

    @staticmethod
    def _strings(value: JsonValue | None) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    @staticmethod
    def _required_string(payload: Mapping[str, JsonValue], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"Forge sidecar response is missing {key}")
        return value

    @staticmethod
    def _status(payload: Mapping[str, JsonValue]) -> str:
        value = payload.get("status")
        if not isinstance(value, str):
            raise RuntimeError("Forge sidecar response is missing status")
        return value

    @staticmethod
    def _optional_positive_int(value: JsonValue | None) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value

    @staticmethod
    def _description(request_data: ForgeClientRequest) -> str:
        for key in ("instruction", "text", "description"):
            value = request_data.arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return request_data.action

    def _result(
        self,
        snapshot: Mapping[str, JsonValue],
        request_data: ForgeClientRequest,
    ) -> ForgeClientResult:
        status_value = self._status(snapshot)
        status_map = {
            "succeeded": ForgeExecutionStatus.SUCCEEDED,
            "failed": ForgeExecutionStatus.FAILED,
            "cancelled": ForgeExecutionStatus.CANCELLED,
            "timed_out": ForgeExecutionStatus.TIMED_OUT,
        }
        try:
            status = status_map[status_value]
        except KeyError as exc:
            raise RuntimeError(f"unsupported Forge sidecar status: {status_value}") from exc

        self._verify_identity(snapshot, request_data)
        artifacts = self._artifacts(snapshot.get("artifacts"))
        output = snapshot.get("output")
        metadata = snapshot.get("metadata")
        result_code = snapshot.get("result_code")
        retry_after = snapshot.get("retry_after_seconds")
        stdout = snapshot.get("stdout")
        stderr = snapshot.get("stderr")
        error_code = snapshot.get("error_code")
        error_message = snapshot.get("error_message")
        return ForgeClientResult(
            status=status,
            execution_id=self._required_string(snapshot, "execution_id"),
            result_code=(
                result_code
                if isinstance(result_code, int) and not isinstance(result_code, bool)
                else None
            ),
            output=cast(dict[str, JsonValue], output) if isinstance(output, dict) else {},
            stdout=stdout if isinstance(stdout, str) else "",
            stderr=stderr if isinstance(stderr, str) else "",
            artifacts=artifacts,
            error_code=error_code if isinstance(error_code, str) else None,
            error_message=error_message if isinstance(error_message, str) else None,
            retryable=snapshot.get("retryable") is True,
            retry_after_seconds=(
                float(retry_after)
                if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool)
                else None
            ),
            metadata=cast(dict[str, JsonValue], metadata) if isinstance(metadata, dict) else {},
        )

    def _verify_identity(
        self,
        snapshot: Mapping[str, JsonValue],
        request_data: ForgeClientRequest,
    ) -> None:
        if self._required_string(snapshot, "request_ref") != request_data.request_ref:
            raise RuntimeError("Forge sidecar returned the wrong request_ref")
        if self._required_string(snapshot, "task_id") != request_data.task_id:
            raise RuntimeError("Forge sidecar returned the wrong task_id")
        if self._required_string(snapshot, "run_id") != request_data.run_id:
            raise RuntimeError("Forge sidecar returned the wrong run_id")
        if snapshot.get("step_id") != request_data.step_id:
            raise RuntimeError("Forge sidecar returned the wrong step_id")

    @staticmethod
    def _artifacts(value: JsonValue | None) -> tuple[ForgeArtifact, ...]:
        if not isinstance(value, list):
            return ()
        artifacts: list[ForgeArtifact] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            relative_path = item.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                continue
            media_type = item.get("media_type")
            size_bytes = item.get("size_bytes")
            artifacts.append(
                ForgeArtifact(
                    relative_path=relative_path,
                    media_type=media_type
                    if isinstance(media_type, str)
                    else "application/octet-stream",
                    size_bytes=(
                        size_bytes
                        if isinstance(size_bytes, int) and not isinstance(size_bytes, bool)
                        else None
                    ),
                )
            )
        return tuple(artifacts)
