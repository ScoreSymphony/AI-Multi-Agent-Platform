from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ai_multi_agent_platform.acceptance import AcceptanceProfile, profile_checks
from ai_multi_agent_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import HealthStatus, ModelRequest, OperationContext


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
    assert Path("tests/test_issue_250_restart_inventory_revalidation.py").as_posix() in rendered
