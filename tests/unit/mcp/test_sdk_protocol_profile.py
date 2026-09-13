"""Local MCP SDK protocol/configuration coverage split from the transport integration suite."""

from __future__ import annotations

import asyncio
import sys

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import MCPPythonSDKClient

_STABLE_PROTOCOL_REVISION = "2025-11-25"


class _FakeConnectedClient:
    def __init__(self, protocol_version: str) -> None:
        self.protocol_version = protocol_version
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> _FakeConnectedClient:
        self.entered += 1
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.exited += 1


@pytest.mark.parametrize(
    "negotiated_revision",
    ("2024-11-05", "2026-07-28", "not-a-protocol-revision", ""),
)
def test_stable_sdk_profile_rejects_incompatible_or_malformed_negotiation(
    negotiated_revision: str,
) -> None:
    client = MCPPythonSDKClient(
        MCPServerConfig(
            server_id="negative-negotiation",
            endpoint="http://127.0.0.1:1/mcp",
            protocol_revision=_STABLE_PROTOCOL_REVISION,
        )
    )
    fake = _FakeConnectedClient(negotiated_revision)
    client._client = lambda: fake  # type: ignore[method-assign]

    assert asyncio.run(client.ping()) is False
    assert fake.entered == 1
    assert fake.exited == 1


def test_stable_sdk_profile_accepts_exact_revision_and_closes_session() -> None:
    client = MCPPythonSDKClient(
        MCPServerConfig(
            server_id="exact-negotiation",
            endpoint="http://127.0.0.1:1/mcp",
            protocol_revision=_STABLE_PROTOCOL_REVISION,
        )
    )
    fake = _FakeConnectedClient(_STABLE_PROTOCOL_REVISION)
    client._client = lambda: fake  # type: ignore[method-assign]

    assert asyncio.run(client.ping()) is True
    assert fake.entered == 1
    assert fake.exited == 1


def test_mcp_config_rejects_ambiguous_transport_targets() -> None:
    try:
        MCPServerConfig(
            server_id="ambiguous",
            endpoint="http://127.0.0.1:9000/mcp",
            command=(sys.executable, "mcp_stdio_server.py"),
        )
    except ValueError as exc:
        assert "exactly one" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("ambiguous MCP transport configuration was accepted")
