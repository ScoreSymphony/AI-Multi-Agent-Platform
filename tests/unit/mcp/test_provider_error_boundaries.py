from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig, MCPTool, MCPToolProvider
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue


class _DiscoveryClient:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def list_tools(self) -> tuple[MCPTool, ...]:
        raise self.failure

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        del name, arguments
        raise AssertionError("call_tool is not part of this test")

    async def ping(self) -> bool:
        return True


class _HealthFailureClient:
    async def list_tools(self) -> tuple[MCPTool, ...]:
        return ()

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        del name, arguments
        raise AssertionError("call_tool is not part of this test")

    async def ping(self) -> bool:
        raise RuntimeError("synthetic provider health detail")


class _CancellingHealthClient(_HealthFailureClient):
    async def ping(self) -> bool:
        raise asyncio.CancelledError


def _provider(client: object) -> MCPToolProvider:
    return MCPToolProvider(
        MCPServerConfig(
            server_id="boundary-test",
            endpoint="http://127.0.0.1:1/mcp",
        ),
        client,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_discovery_preserves_existing_canonical_contract_error() -> None:
    canonical = ContractError(
        ErrorCode.CONTRACT_VIOLATION,
        "negotiated protocol revision is incompatible",
        provider_id="mcp:boundary-test",
    )
    provider = _provider(_DiscoveryClient(canonical))

    with pytest.raises(ContractError) as caught:
        await provider.capability_registrations()

    assert caught.value is canonical
    assert caught.value.code is ErrorCode.CONTRACT_VIOLATION


@pytest.mark.asyncio
async def test_discovery_translates_unknown_client_failure_without_public_detail_leak() -> None:
    provider = _provider(_DiscoveryClient(RuntimeError("synthetic-secret-provider-detail")))

    with pytest.raises(ContractError) as caught:
        await provider.capability_registrations()

    assert caught.value.code is ErrorCode.UNAVAILABLE
    assert caught.value.provider_id == "mcp:boundary-test"
    assert caught.value.retryable is True
    assert "synthetic-secret-provider-detail" not in caught.value.message
    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_health_contains_ordinary_client_failure_as_unavailable() -> None:
    provider = _provider(_HealthFailureClient())

    assert await provider.health() is HealthStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_health_does_not_convert_cancellation_to_unavailable() -> None:
    provider = _provider(_CancellingHealthClient())

    with pytest.raises(asyncio.CancelledError):
        await provider.health()
