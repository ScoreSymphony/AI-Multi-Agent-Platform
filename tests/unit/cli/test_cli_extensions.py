from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class ExtensionTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del headers, body, timeout
        path = urlsplit(url).path
        self.calls.append((method, path))

        if path == "/api/v1/openapi.json":
            payload: object = {
                "openapi": "3.1.0",
                "x-registered-extension-collections": ["capabilities", "knowledge-sources"],
                "x-registered-extension-commands": [
                    "capability.validate",
                    "knowledge.refresh",
                ],
            }
        elif path == "/api/v1/capabilities":
            payload = {
                "items": [
                    {
                        "id": "capability_test",
                        "name": "test capability",
                        "health": "healthy",
                    }
                ],
                "total": 1,
                "next_cursor": None,
                "limit": 50,
            }
        elif path == "/api/v1/capabilities/capability_test":
            payload = {
                "id": "capability_test",
                "name": "test capability",
                "health": "healthy",
            }
        else:
            raise AssertionError(f"unexpected request: {method} {path}")

        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: ExtensionTransport,
    *arguments: str,
) -> tuple[int, dict[str, object], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def test_extension_surface_is_discovered_from_canonical_openapi(tmp_path: Path) -> None:
    transport = ExtensionTransport()
    config = tmp_path / "cli.json"

    code, collections, error = _invoke(config, transport, "extension", "collections")
    assert code == 0 and not error
    assert collections["data"] == {
        "items": [{"name": "capabilities"}, {"name": "knowledge-sources"}],
        "total": 2,
    }

    code, commands, error = _invoke(config, transport, "extension", "commands")
    assert code == 0 and not error
    assert commands["data"] == {
        "items": [{"name": "capability.validate"}, {"name": "knowledge.refresh"}],
        "total": 2,
    }

    assert all(method == "GET" for method, _ in transport.calls)


def test_extension_resources_are_read_only_and_require_registration(tmp_path: Path) -> None:
    transport = ExtensionTransport()
    config = tmp_path / "cli.json"

    code, listed, error = _invoke(config, transport, "extension", "list", "capabilities")
    assert code == 0 and not error
    assert listed["data"]["total"] == 1  # type: ignore[index]
    assert transport.calls[-1] == ("GET", "/api/v1/capabilities")

    code, shown, error = _invoke(
        config,
        transport,
        "extension",
        "show",
        "capabilities",
        "capability_test",
    )
    assert code == 0 and not error
    assert shown["data"]["id"] == "capability_test"  # type: ignore[index]
    assert transport.calls[-1] == ("GET", "/api/v1/capabilities/capability_test")

    previous_calls = len(transport.calls)
    code, payload, error = _invoke(config, transport, "extension", "list", "workers")
    assert code == 2
    assert not payload
    assert "not registered" in error
    assert len(transport.calls) == previous_calls + 1
    assert transport.calls[-1] == ("GET", "/api/v1/openapi.json")

    assert all(method == "GET" for method, _ in transport.calls)
