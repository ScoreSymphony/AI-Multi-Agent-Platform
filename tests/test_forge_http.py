from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.adapters.forge import ForgeClientRequest, ForgeExecutionStatus
from ai_multi_agent_platform.adapters.forge_http import (
    ForgeHttpClient,
    ForgeHttpClientConfig,
    ForgeHttpResponse,
)
from ai_multi_agent_platform.contracts.types import JsonValue


class ScriptedTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, JsonValue] | None]] = []
        self.status_reads = 0

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> ForgeHttpResponse:
        assert timeout_seconds == 2.0
        self.calls.append((method, url, payload))
        if url.endswith("/healthz"):
            return ForgeHttpResponse(
                200,
                {
                    "healthy": True,
                    "protocol_version": "forge-executor-sidecar/v1",
                    "allowed_executor_types": ["null"],
                    "executors": [{"executor_type": "null", "status": "authenticated"}],
                },
            )
        if method == "POST" and url.endswith("/v1/executions"):
            assert payload is not None
            assert payload["request_ref"] == "run_1"
            assert payload["executor_type"] == "null"
            assert payload["description"] == "perform the canonical step"
            return ForgeHttpResponse(202, self._snapshot("running"))
        if method == "GET" and "/v1/executions/forge_exec_1" in url:
            self.status_reads += 1
            return ForgeHttpResponse(200, self._snapshot("succeeded"))
        if method == "GET" and "/v1/requests/run_1" in url:
            return ForgeHttpResponse(200, self._snapshot("running"))
        if method == "POST" and url.endswith("/v1/executions/forge_exec_1/cancel"):
            return ForgeHttpResponse(200, self._snapshot("cancelled"))
        raise AssertionError(f"unexpected request: {method} {url}")

    @staticmethod
    def _snapshot(status: str) -> dict[str, JsonValue]:
        return {
            "protocol_version": "forge-executor-sidecar/v1",
            "request_ref": "run_1",
            "execution_id": "forge_exec_1",
            "task_id": "task_1",
            "run_id": "run_1",
            "step_id": "step_1",
            "correlation_id": "task_1",
            "status": status,
            "result_code": 0 if status == "succeeded" else None,
            "output": {"summary": "done"},
            "stdout": "out",
            "stderr": "",
            "artifacts": [],
            "error_code": None,
            "error_message": None,
            "retryable": False,
            "retry_after_seconds": None,
            "metadata": {"executor_type": "null"},
        }


def client(transport: ScriptedTransport) -> ForgeHttpClient:
    return ForgeHttpClient(
        ForgeHttpClientConfig(
            base_url="http://127.0.0.1:8787/",
            executor_type="null",
            executor_config={"delay_seconds": 0},
            poll_interval_seconds=0.001,
            request_timeout_seconds=2.0,
        ),
        transport=transport,
    )


def forge_request() -> ForgeClientRequest:
    return ForgeClientRequest(
        request_ref="run_1",
        task_id="task_1",
        run_id="run_1",
        step_id="step_1",
        correlation_id="task_1",
        action="execute",
        workspace_path="/tmp/workspaces/task_1",
        arguments={"instruction": "perform the canonical step"},
        policy_context={"max_turns": 3},
    )


def test_health_reports_configured_sidecar_executor() -> None:
    transport = ScriptedTransport()
    health = asyncio.run(client(transport).health())
    assert health.healthy is True
    assert health.capabilities == ("execute",)
    assert health.metadata["protocol_version"] == "forge-executor-sidecar/v1"
    assert health.metadata["protocol_compatible"] is True
    assert health.metadata["executor_type"] == "null"


def test_execute_submits_polls_and_preserves_identity() -> None:
    transport = ScriptedTransport()
    result = asyncio.run(client(transport).execute(forge_request()))
    assert result.status is ForgeExecutionStatus.SUCCEEDED
    assert result.execution_id == "forge_exec_1"
    assert result.result_code == 0
    assert result.output == {"summary": "done"}
    assert result.stdout == "out"
    assert result.metadata["executor_type"] == "null"
    assert transport.status_reads == 1


def test_cancel_resolves_request_ref_to_backend_execution() -> None:
    transport = ScriptedTransport()
    asyncio.run(client(transport).cancel("run_1"))
    assert [call[0] for call in transport.calls] == ["GET", "POST"]
    assert transport.calls[-1][1].endswith("/v1/executions/forge_exec_1/cancel")


def test_health_is_false_when_configured_executor_is_not_allowed() -> None:
    class RestrictedTransport(ScriptedTransport):
        async def request_json(
            self,
            method: str,
            url: str,
            *,
            payload: Mapping[str, JsonValue] | None,
            timeout_seconds: float,
        ) -> ForgeHttpResponse:
            if url.endswith("/healthz"):
                return ForgeHttpResponse(
                    200,
                    {
                        "healthy": True,
                        "protocol_version": "forge-executor-sidecar/v1",
                        "allowed_executor_types": ["codex"],
                        "executors": [{"executor_type": "null", "status": "authenticated"}],
                    },
                )
            return await super().request_json(
                method,
                url,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )

    health = asyncio.run(client(RestrictedTransport()).health())
    assert health.healthy is False


def test_health_is_false_for_incompatible_protocol() -> None:
    class IncompatibleTransport(ScriptedTransport):
        async def request_json(
            self,
            method: str,
            url: str,
            *,
            payload: Mapping[str, JsonValue] | None,
            timeout_seconds: float,
        ) -> ForgeHttpResponse:
            if url.endswith("/healthz"):
                return ForgeHttpResponse(
                    200,
                    {
                        "healthy": True,
                        "protocol_version": "forge-executor-sidecar/v2",
                        "allowed_executor_types": ["null"],
                        "executors": [{"executor_type": "null", "status": "authenticated"}],
                    },
                )
            return await super().request_json(
                method,
                url,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )

    health = asyncio.run(client(IncompatibleTransport()).health())
    assert health.healthy is False
    assert health.metadata["protocol_compatible"] is False


def test_execute_rejects_incompatible_protocol() -> None:
    class IncompatibleExecutionTransport(ScriptedTransport):
        async def request_json(
            self,
            method: str,
            url: str,
            *,
            payload: Mapping[str, JsonValue] | None,
            timeout_seconds: float,
        ) -> ForgeHttpResponse:
            if method == "POST" and url.endswith("/v1/executions"):
                snapshot = self._snapshot("succeeded")
                snapshot["protocol_version"] = "forge-executor-sidecar/v2"
                return ForgeHttpResponse(202, snapshot)
            return await super().request_json(
                method,
                url,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )

    with pytest.raises(RuntimeError, match="incompatible protocol"):
        asyncio.run(client(IncompatibleExecutionTransport()).execute(forge_request()))
