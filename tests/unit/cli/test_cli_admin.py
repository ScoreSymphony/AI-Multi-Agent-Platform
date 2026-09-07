from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

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
        path = urlsplit(url).path
        self.calls.append((method, path, dict(headers), body))
        response_body: object
        if method == "GET" and path.endswith("/model-providers"):
            response_body = {
                "items": [
                    {
                        "id": "provider_test",
                        "enabled": True,
                        "health": "healthy",
                    }
                ],
                "next_cursor": None,
                "total": 1,
                "limit": 50,
            }
        elif method == "GET" and "/models/" in path:
            response_body = {"id": "model_test", "alias": "test", "enabled": True}
        elif method == "GET" and path.endswith("/plans"):
            response_body = {"items": [], "next_cursor": None, "total": 0, "limit": 50}
        elif method == "GET" and "/artifacts/" in path:
            response_body = {"id": "artifact_test", "task_id": "task_test"}
        elif method == "POST" and "/model-providers/" in path:
            response_body = {"id": "provider_test", "enabled": False}
        elif method == "POST" and "/tasks/" in path:
            response_body = {"id": "task_test", "status": "cancelled"}
        else:
            response_body = {"ok": True}
        return RawResponse(
            status=200,
            body=json.dumps(response_body).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: RecordingTransport,
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


def test_model_and_reference_commands_use_canonical_control_plane_paths(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = RecordingTransport()

    code, providers, error = _invoke(config, transport, "model-provider", "list")
    assert code == 0 and not error
    assert providers["data"]["total"] == 1  # type: ignore[index]
    assert transport.calls[-1][0:2] == ("GET", "/api/v1/model-providers")

    code, model, error = _invoke(config, transport, "model", "show", "test")
    assert code == 0 and not error
    assert model["data"]["alias"] == "test"  # type: ignore[index]
    assert transport.calls[-1][0:2] == ("GET", "/api/v1/models/test")

    code, plans, error = _invoke(config, transport, "plan", "list")
    assert code == 0 and not error
    assert plans["data"]["total"] == 0  # type: ignore[index]
    assert transport.calls[-1][0:2] == ("GET", "/api/v1/plans")

    code, artifact, error = _invoke(config, transport, "artifact", "show", "artifact_test")
    assert code == 0 and not error
    assert artifact["data"]["id"] == "artifact_test"  # type: ignore[index]
    assert transport.calls[-1][0:2] == ("GET", "/api/v1/artifacts/artifact_test")


def test_model_provider_mutation_requires_confirmation_and_uses_idempotency_key(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport = RecordingTransport()

    code, payload, error = _invoke(
        config,
        transport,
        "model-provider",
        "disable",
        "provider_test",
    )
    assert code == 2
    assert not payload
    assert "requires confirmation" in error
    assert transport.calls == []

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "model-provider",
        "disable",
        "provider_test",
        "--idempotency-key",
        "provider-disable-test",
    )
    assert code == 0 and not error
    assert payload["data"]["enabled"] is False  # type: ignore[index]
    method, path, headers, _ = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/model-providers/provider_test:disable"
    assert headers["idempotency-key"] == "provider-disable-test"


def test_task_cancel_uses_same_confirmation_boundary(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = RecordingTransport()

    code, payload, error = _invoke(config, transport, "task", "cancel", "task_test")
    assert code == 2
    assert not payload
    assert "requires confirmation" in error
    assert transport.calls == []

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "task",
        "cancel",
        "task_test",
    )
    assert code == 0 and not error
    assert payload["data"]["status"] == "cancelled"  # type: ignore[index]
    assert transport.calls[-1][0:2] == ("POST", "/api/v1/tasks/task_test:cancel")
