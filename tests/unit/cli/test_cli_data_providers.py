from __future__ import annotations

import json
from pathlib import Path

from cli_test_helpers import ControlPlaneRecordingTransport as RecordingTransport
from cli_test_helpers import invoke_cli_json as _invoke
from cli_test_helpers import page_items as _items

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP
from ai_multi_agent_platform.data import (
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


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
