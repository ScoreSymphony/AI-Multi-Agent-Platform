from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli

_RELEASE = {
    "id": "application_release_11111111-1111-1111-1111-111111111111",
    "type": "application_release",
    "application_id": "example-app",
    "version": "1.0.0",
    "channel": "stable",
    "visibility": "public",
    "status": "published",
    "source_revision": "7" * 40,
    "targets": [
        {
            "target": {
                "target_id": "linux-x64",
                "os_name": "linux",
                "architecture": "x86_64",
                "package_type": "archive",
            },
            "status": "succeeded",
        }
    ],
    "artifacts": [
        {
            "artifact_id": "artifact_22222222-2222-2222-2222-222222222222",
            "target_id": "linux-x64",
            "filename": "example-app-linux-x64.tar.gz",
            "sha256": "8" * 64,
            "download_url": (
                "https://downloads.example/releases/v1.0.0/example-app-linux-x64.tar.gz"
            ),
            "external_metadata": {
                "worker_id": "worker_issue751_linux",
                "reference": {"asset_id": "asset:example"},
            },
        }
    ],
    "release_url": "https://downloads.example/releases/v1.0.0",
    "latest_url": "https://downloads.example/releases/latest",
    "external_metadata": {"reference": {"release_id": "release:issue751"}},
}


class _ApplicationReleaseTransport:
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
                "openapi": "3.1.0",
                "x-registered-extension-collections": ["application-releases"],
                "x-registered-extension-commands": [
                    "application-release.create",
                    "application-release.build",
                    "application-release.preview",
                    "application-release.publish",
                ],
            }
        elif path == "/api/v1/application-releases":
            payload = {
                "items": [_RELEASE],
                "total": 1,
                "next_cursor": None,
                "limit": 50,
            }
        elif path == f"/api/v1/application-releases/{_RELEASE['id']}":
            payload = _RELEASE
        else:
            raise AssertionError(f"unexpected application-release CLI request: {method} {path}")
        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: _ApplicationReleaseTransport,
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
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(output, dict)
    return code, output, stderr.getvalue()


def test_generic_cli_preserves_application_release_list_and_show_state(
    tmp_path: Path,
) -> None:
    transport = _ApplicationReleaseTransport()
    config = tmp_path / "cli.json"

    code, listed, error = _invoke(
        config,
        transport,
        "extension",
        "list",
        "application-releases",
    )
    assert code == 0 and not error
    data = listed["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert items == [_RELEASE]

    code, shown, error = _invoke(
        config,
        transport,
        "extension",
        "show",
        "application-releases",
        str(_RELEASE["id"]),
    )
    assert code == 0 and not error
    assert shown["data"] == _RELEASE
    assert transport.calls == [
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/application-releases"),
        ("GET", "/api/v1/openapi.json"),
        ("GET", f"/api/v1/application-releases/{_RELEASE['id']}"),
    ]
