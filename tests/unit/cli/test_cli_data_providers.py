from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import (
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
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
        del timeout
        parsed = urlsplit(url)
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append((method, parsed.path))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _http(*, providers: DataProviderSet | None = None) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=None if providers is None else data_resource_services(providers),
    )
    return ControlPlaneHTTP(control_plane)


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
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


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_data_provider_cli_reads_public_health_and_capability_metadata(tmp_path: Path) -> None:
    transport = RecordingTransport(_http(providers=_providers(tmp_path)))
    config = tmp_path / "cli.json"

    code, page, error = _invoke(
        config,
        transport,
        "extension",
        "list",
        "data-providers",
    )
    assert code == 0 and not error
    providers = {item["role"]: item for item in _items(page)}
    assert set(providers) == {"file", "memory", "knowledge"}

    assert providers["file"]["id"] == "local-file-reference"
    assert providers["file"]["health"] == "healthy"
    assert providers["file"]["available"] is True
    assert "checksum" in providers["file"]["supported_operations"]
    assert "sha256" in providers["file"]["capabilities"][0]["features"]

    assert providers["memory"]["id"] == "local-memory-reference"
    assert providers["memory"]["health"] == "healthy"
    assert "seven_scopes" in providers["memory"]["capabilities"][0]["features"]
    assert "memory_origin" in providers["memory"]["capabilities"][0]["features"]

    assert providers["knowledge"]["id"] == "local-knowledge-reference"
    assert providers["knowledge"]["health"] == "healthy"
    assert "keyword_search" in providers["knowledge"]["supported_operations"]
    assert "get_source" in providers["knowledge"]["supported_operations"]
    assert "list_sources" in providers["knowledge"]["supported_operations"]

    code, memory, error = _invoke(
        config,
        transport,
        "extension",
        "show",
        "data-providers",
        "local-memory-reference",
    )
    assert code == 0 and not error
    assert memory["data"]["role"] == "memory"
    assert memory["data"]["provider_type"] == "memory"

    serialized = json.dumps(page, sort_keys=True)
    assert "adapter_metadata" not in serialized
    assert "backend_ref" not in serialized

    assert transport.calls == [
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/data-providers"),
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/data-providers/local-memory-reference"),
    ]


def test_data_provider_cli_has_no_provider_fallback_when_collection_is_absent(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport(_http())
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(
        config,
        transport,
        "extension",
        "list",
        "data-providers",
    )

    assert code == 2
    assert payload == {}
    assert "canonical extension collection is not registered: data-providers" in error
    assert transport.calls == [("GET", "/api/v1/openapi.json")]
