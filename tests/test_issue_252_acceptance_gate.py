from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ai_multi_agent_platform.acceptance import AcceptanceProfile, profile_checks
from ai_multi_agent_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.cli.client import (
    ClientOptions,
    ControlPlaneClient,
    RawResponse,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id


class _LocalModelHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._json(200, {"data": [{"id": "local-fixture-model"}]})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        assert payload["model"] == "local-fixture-model"
        self._json(
            200,
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "local acceptance response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3},
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FixtureTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
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
        self.calls.append((method, url))
        return RawResponse(
            status=200,
            body=json.dumps(self.payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def test_local_ai_profile_uses_real_loopback_openai_compatible_endpoint() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleProviderConfig(
                provider_id="local-acceptance",
                base_url=f"http://{host}:{port}/v1",
                models={"model-local-acceptance": "local-fixture-model"},
                timeout_seconds=5.0,
            )
        )
        assert asyncio.run(provider.health()) is HealthStatus.HEALTHY
        assert asyncio.run(provider.list_native_models()) == ("local-fixture-model",)
        response = asyncio.run(
            provider.generate(
                ModelRequest(
                    request_id="request-252-local-ai",
                    messages=("Return one local response.",),
                    context=OperationContext(correlation_id="correlation-252-local-ai"),
                    requirements={"model_config_id": "model-local-acceptance"},
                )
            )
        )
        assert response.model_ref == "model-local-acceptance"
        assert response.text == "local acceptance response"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_memory_acceptance_create_retrieve_provenance_delete_not_found(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    operation = OperationContext(
        correlation_id="correlation-252-memory",
        owner_type="user",
        owner_id="user-a",
    )
    context = DataAccessContext(operation=operation, actor_ref="user:user-a")
    source_task_id = new_id("task")
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"fact": "prototype acceptance"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        provenance=(SourceRef(kind="task", ref=source_task_id),),
    )

    stored = asyncio.run(provider.write_entry(entry, context))
    retrieved = asyncio.run(provider.get_entry(stored.memory_id, context))
    assert retrieved.value == {"fact": "prototype acceptance"}
    assert retrieved.provenance == (SourceRef(kind="task", ref=source_task_id),)
    assert asyncio.run(
        provider.query_entries(MemoryQuery(MemoryScope.USER, "user-a"), context)
    ) == (retrieved,)

    asyncio.run(provider.delete_entry(retrieved.memory_id, context))
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(retrieved.memory_id, context))
    assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_cli_and_web_share_canonical_task_fixture_and_route() -> None:
    fixture_path = Path("frontend/src/api/__fixtures__/canonical-task.json")
    canonical_task = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert isinstance(canonical_task, dict)
    task_id = canonical_task.get("id")
    assert isinstance(task_id, str)

    transport = _FixtureTransport(canonical_task)
    client = ControlPlaneClient(
        ClientOptions(endpoint="http://control-plane.invalid", retries=0),
        transport=transport,
    )
    response = client.get(f"/tasks/{task_id}")

    assert response.body == canonical_task
    assert response.api_version == "v1"
    assert transport.calls == [
        ("GET", f"http://control-plane.invalid/api/v1/tasks/{task_id}"),
    ]


def test_acceptance_profiles_are_explicit_and_owner_attributed() -> None:
    assert {profile.value for profile in AcceptanceProfile} == {
        "reference",
        "local-ai",
        "degraded",
        "persistence",
    }
    for profile in AcceptanceProfile:
        checks = profile_checks(profile)
        assert checks
        assert len({check.check_id for check in checks}) == len(checks)
        assert all(check.owner.startswith("#") for check in checks)
        assert all(check.criterion for check in checks)
        assert all(check.command for check in checks)


def test_acceptance_registry_uses_only_public_repo_surfaces() -> None:
    forbidden = ("docker exec", "sqlite3 ", "/private/", "localhost:11434", "api.openai.com")
    rendered = "\n".join(
        " ".join(check.command)
        for profile in AcceptanceProfile
        for check in profile_checks(profile)
    ).lower()
    assert all(value not in rendered for value in forbidden)
    assert "canonicalstateparity.test.ts" in rendered
    assert "test_cli_and_web_share_canonical_task_fixture_and_route" in rendered
    assert "test_memory_acceptance_create_retrieve_provenance_delete_not_found" in rendered
    assert Path("tests/test_issue_250_restart_inventory_revalidation.py").as_posix() in rendered
