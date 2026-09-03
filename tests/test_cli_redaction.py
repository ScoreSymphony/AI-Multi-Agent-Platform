from __future__ import annotations

import json
from io import StringIO

from ai_multi_agent_platform.cli.client import APIClientError, ClientResponse
from ai_multi_agent_platform.cli.render import Renderer
from ai_multi_agent_platform.security.redaction import REDACTED


def test_json_success_recursively_redacts_sensitive_response_fields() -> None:
    stdout = StringIO()
    renderer = Renderer(json_mode=True, verbose=False, stdout=stdout)

    renderer.success(
        ClientResponse(
            status=200,
            body={
                "id": "provider_test",
                "api_key": "plaintext-key",
                "nested": {
                    "access_token": "plaintext-token",
                    "safe": "visible",
                },
            },
            request_id="request_test",
            correlation_id="corr_test",
            api_version="v1",
        )
    )

    payload = json.loads(stdout.getvalue())
    assert payload["data"]["api_key"] == REDACTED
    assert payload["data"]["nested"]["access_token"] == REDACTED
    assert payload["data"]["nested"]["safe"] == "visible"
    assert "plaintext-key" not in stdout.getvalue()
    assert "plaintext-token" not in stdout.getvalue()


def test_human_success_redacts_sensitive_response_fields() -> None:
    stdout = StringIO()
    renderer = Renderer(json_mode=False, verbose=False, stdout=stdout)

    renderer.local_success(
        {
            "client_secret": "plaintext-client-secret",
            "credential": "plaintext-credential",
            "status": "configured",
        }
    )

    rendered = stdout.getvalue()
    assert "plaintext-client-secret" not in rendered
    assert "plaintext-credential" not in rendered
    assert f"client_secret: {REDACTED}" in rendered
    assert f"credential: {REDACTED}" in rendered
    assert "status: configured" in rendered


def test_json_error_redacts_nested_sensitive_details() -> None:
    stderr = StringIO()
    renderer = Renderer(json_mode=True, verbose=False, stderr=stderr)

    renderer.error(
        APIClientError(
            status=422,
            code="invalid_configuration",
            category="configuration",
            message="provider configuration is invalid",
            retryable=False,
            request_id="request_test",
            correlation_id="corr_test",
            details={
                "provider": "test",
                "password": "plaintext-password",
                "nested": {"private_key": "plaintext-private-key"},
            },
        )
    )

    payload = json.loads(stderr.getvalue())
    assert payload["details"]["password"] == REDACTED
    assert payload["details"]["nested"]["private_key"] == REDACTED
    assert payload["details"]["provider"] == "test"
    assert "plaintext-password" not in stderr.getvalue()
    assert "plaintext-private-key" not in stderr.getvalue()
