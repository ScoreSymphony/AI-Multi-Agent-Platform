from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class OnboardingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict[str, object] | None]] = []

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
        payload = json.loads(body.decode("utf-8")) if body is not None else None
        assert payload is None or isinstance(payload, dict)
        self.calls.append((method, path, dict(headers), payload))

        if path == "/api/v1/onboarding/first-run":
            response: object = {
                "id": "first-run",
                "type": "onboarding_status",
                "state": "needs_model",
                "guidance": ["Configure an explicit local or self-hosted ModelProvider."],
            }
        elif path == "/api/v1/commands/onboarding.configure-model":
            response = {
                "id": "model-qwen-local",
                "type": "model",
                "location": "local",
                "credential_mode": "secret_reference",
            }
        elif path == "/api/v1/commands/onboarding.run-first-task":
            response = {
                "id": "result_00000000-0000-4000-8000-000000000250",
                "type": "first_run_result",
                "task_id": "task_00000000-0000-4000-8000-000000000250",
                "run_id": "run_00000000-0000-4000-8000-000000000250",
                "result_id": "result_00000000-0000-4000-8000-000000000250",
            }
        else:
            raise AssertionError(f"unexpected request: {method} {path}")

        return RawResponse(
            status=200,
            body=json.dumps(response).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: OnboardingTransport,
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


def test_onboarding_status_uses_only_canonical_control_plane_api(tmp_path: Path) -> None:
    transport = OnboardingTransport()

    code, payload, error = _invoke(
        tmp_path / "cli.json",
        transport,
        "onboarding",
        "status",
    )

    assert code == 0 and not error
    assert payload["data"]["state"] == "needs_model"  # type: ignore[index]
    assert len(transport.calls) == 1
    method, path, _headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/onboarding/first-run"
    assert body is None


def test_configure_model_sends_secret_reference_but_never_secret_value(tmp_path: Path) -> None:
    transport = OnboardingTransport()
    credential_ref = {
        "provider": "local",
        "secret_id": "model-endpoint-token",
        "scope": "user:user-alice",
    }

    code, payload, error = _invoke(
        tmp_path / "cli.json",
        transport,
        "onboarding",
        "configure-model",
        "--adapter-id",
        "openai-compatible",
        "--provider-id",
        "local-openai",
        "--model-id",
        "model-qwen-local",
        "--provider-model",
        "qwen-local",
        "--base-url",
        "http://127.0.0.1:8001/v1",
        "--location",
        "local",
        "--capabilities-json",
        '{"modalities":["text"],"tool_calling":false}',
        "--credential-ref-json",
        json.dumps(credential_ref),
        "--idempotency-key",
        "issue-250-cli-model",
    )

    assert code == 0 and not error
    assert payload["data"]["credential_mode"] == "secret_reference"  # type: ignore[index]
    assert len(transport.calls) == 1
    method, path, headers, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/v1/commands/onboarding.configure-model"
    assert headers["Idempotency-Key"] == "issue-250-cli-model"
    assert body == {
        "resource_ref": "first-run",
        "adapter_id": "openai-compatible",
        "provider_id": "local-openai",
        "model_config_id": "model-qwen-local",
        "provider_model": "qwen-local",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {"modalities": ["text"], "tool_calling": False},
        "credential_ref": credential_ref,
    }
    serialized = json.dumps(body, sort_keys=True)
    assert "secret_value" not in serialized
    assert "bearer_token" not in serialized
    assert "api_key" not in serialized


def test_run_first_task_uses_same_control_plane_command_surface(tmp_path: Path) -> None:
    transport = OnboardingTransport()

    code, payload, error = _invoke(
        tmp_path / "cli.json",
        transport,
        "onboarding",
        "run-first-task",
        "--objective",
        "Return one short local response.",
        "--project-id",
        "project_00000000-0000-4000-8000-000000000250",
        "--workspace-id",
        "workspace_00000000-0000-4000-8000-000000000250",
        "--agent-id",
        "agent_00000000-0000-4000-8000-000000000250",
        "--idempotency-key",
        "issue-250-cli-first-task",
    )

    assert code == 0 and not error
    assert payload["data"]["result_id"].startswith("result_")  # type: ignore[index,union-attr]
    assert len(transport.calls) == 1
    method, path, headers, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/v1/commands/onboarding.run-first-task"
    assert headers["Idempotency-Key"] == "issue-250-cli-first-task"
    assert body == {
        "resource_ref": "first-run",
        "objective": "Return one short local response.",
        "project_id": "project_00000000-0000-4000-8000-000000000250",
        "workspace_id": "workspace_00000000-0000-4000-8000-000000000250",
        "agent_id": "agent_00000000-0000-4000-8000-000000000250",
    }


def test_onboarding_cli_rejects_non_object_json_before_transport(tmp_path: Path) -> None:
    transport = OnboardingTransport()

    code, payload, error = _invoke(
        tmp_path / "cli.json",
        transport,
        "onboarding",
        "configure-model",
        "--adapter-id",
        "openai-compatible",
        "--provider-id",
        "local-openai",
        "--model-id",
        "model-qwen-local",
        "--provider-model",
        "qwen-local",
        "--base-url",
        "http://127.0.0.1:8001/v1",
        "--location",
        "local",
        "--credential-ref-json",
        '["not-a-secret-reference"]',
    )

    assert code == 2
    assert not payload
    assert "--credential-ref-json must contain a JSON object" in error
    assert not transport.calls
