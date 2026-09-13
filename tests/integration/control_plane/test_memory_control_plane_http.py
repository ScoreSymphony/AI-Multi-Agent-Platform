from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import (
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _http(tmp_path: Path) -> tuple[ControlPlane, ControlPlaneHTTP]:
    providers = _providers(tmp_path)
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=data_resource_services(providers),
        command_handlers=data_command_handlers(providers),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-memory-251",
        "X-Correlation-Id": "correlation-memory-251",
        "X-Principal-Ref": "user:user-a",
        "X-Owner-Type": "user",
        "X-Owner-Id": "user-a",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def test_memory_content_is_versioned_northbound_resource_with_idempotent_commands(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        control_plane, http = _http(tmp_path)
        assert "memory" in control_plane.registered_collections
        assert "knowledge" in control_plane.registered_collections
        assert "knowledge-results" in control_plane.registered_collections
        assert "memory.create" in control_plane.registered_commands
        assert "knowledge.register" in control_plane.registered_commands

        missing_key = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/memory.create",
                headers=_headers(),
                body={
                    "resource_ref": "user-a",
                    "scope": "user",
                    "origin": "user-authored",
                    "value": {"preference": "provider-neutral"},
                },
            )
        )
        assert missing_key.status == 400
        assert missing_key.body["code"] == "invalid_request"

        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/memory.create",
                headers=_headers(idempotency_key="memory-create-251"),
                body={
                    "resource_ref": "user-a",
                    "scope": "user",
                    "origin": "user-authored",
                    "value": {"preference": "provider-neutral"},
                },
            )
        )
        assert created.status == 200
        memory_id = created.body["id"]
        assert isinstance(memory_id, str)
        assert created.body["origin"] == "user-authored"

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/memory",
                headers=_headers(),
            )
        )
        assert listed.status == 200
        assert listed.body["items"][0]["id"] == memory_id
        assert listed.body["items"][0]["value"] == {"preference": "provider-neutral"}

    asyncio.run(scenario())
