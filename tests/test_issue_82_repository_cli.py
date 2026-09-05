from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class _RepositoryExtensionTransport:
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
                "x-registered-extension-collections": ["repositories"],
                "x-registered-extension-commands": [
                    "repository.status",
                    "repository.diff",
                    "repository.fetch",
                    "repository.branch.create",
                    "repository.checkout",
                    "repository.commit",
                    "repository.push",
                ],
            }
        elif path == "/api/v1/repositories":
            payload = {
                "items": [
                    {
                        "id": "external_resource_repository-fixture",
                        "resource_type": "repository",
                        "default_branch": "main",
                    }
                ],
                "next_cursor": None,
                "total": 1,
                "limit": 50,
            }
        else:
            raise AssertionError(f"unexpected repository CLI path: {path}")
        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def test_repository_collection_is_reachable_through_canonical_cli_extension_hook(
    tmp_path: Path,
) -> None:
    transport = _RepositoryExtensionTransport()
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "extension",
            "list",
            "repositories",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    payload = json.loads(stdout.getvalue())
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["default_branch"] == "main"
    assert transport.calls == [
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/repositories"),
    ]
