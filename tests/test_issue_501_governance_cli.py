from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class GovernanceExtensionTransport:
    def __init__(self) -> None:
        self.command_body: object | None = None
        self.idempotency_key: str | None = None

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
        if path == "/api/v1/openapi.json":
            payload: object = {
                "openapi": "3.1.0",
                "x-registered-extension-collections": ["proposals", "specifications"],
                "x-registered-extension-commands": ["specification.convert-to-task"],
            }
        elif path == "/api/v1/commands/specification.convert-to-task":
            assert method == "POST"
            self.command_body = json.loads(body or b"{}")
            self.idempotency_key = headers.get("Idempotency-Key")
            payload = {"id": "task_test", "type": "task", "status": "draft"}
        else:
            raise AssertionError(f"unexpected request: {method} {path}")
        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def test_extension_execute_uses_registered_command_and_canonical_mutation_contract(
    tmp_path: Path,
) -> None:
    transport = GovernanceExtensionTransport()
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "extension",
            "execute",
            "specification.convert-to-task",
            "specification_test",
            "--payload",
            '{"approval_id":"approval_test"}',
            "--idempotency-key",
            "governance-cli-convert",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 0 and not stderr.getvalue()
    assert transport.command_body == {
        "resource_ref": "specification_test",
        "approval_id": "approval_test",
    }
    assert transport.idempotency_key == "governance-cli-convert"


def test_extension_execute_rejects_unregistered_command_before_post(tmp_path: Path) -> None:
    transport = GovernanceExtensionTransport()
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "extension",
            "execute",
            "specification.bypass",
            "specification_test",
            "--idempotency-key",
            "governance-cli-reject",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 2
    assert "not registered" in stderr.getvalue()
    assert transport.command_body is None
