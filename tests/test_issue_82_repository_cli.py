from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.issue_82 import run_cli
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


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


class _RepositoryCommandTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict[str, str], dict[str, Any]]] = []

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
        parsed = urlsplit(url)
        decoded: dict[str, Any] = {}
        if body is not None:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append(
            (
                method,
                parsed.path,
                dict(parse_qsl(parsed.query)),
                {key.casefold(): value for key, value in headers.items()},
                decoded,
            )
        )
        return RawResponse(
            status=200,
            body=json.dumps({"ok": True, "path": parsed.path}).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _profile(config: Path) -> None:
    store = ProfileStore.load(config)
    store.set_profile(
        "local",
        CLIProfile(
            endpoint="http://control.local",
            principal_ref="user:repository-cli",
            owner_type="user",
            owner_id="repository-cli",
        ),
    )
    store.use("local")
    store.save()


def _invoke(
    config: Path,
    transport: _RepositoryCommandTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
        stdin=StringIO(""),
    )
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else {}
    assert isinstance(output, dict)
    assert isinstance(error, dict)
    return code, output, error


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


def test_repository_cli_lists_and_inspects_through_canonical_control_plane(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RepositoryCommandTransport()

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "list",
        "--limit",
        "25",
        "--connection-id",
        "connection_example",
    )
    assert code == 0 and not error
    method, path, query, headers, body = transport.calls[-1]
    assert (method, path) == ("GET", "/api/v1/repositories")
    assert query == {"limit": "25", "filter.connection_id": "connection_example"}
    assert headers["x-principal-ref"] == "user:repository-cli"
    assert body == {}

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "commits",
        "external_resource_repository",
        "--revision",
        "main",
        "--limit",
        "12",
    )
    assert code == 0 and not error
    method, path, _, _, body = transport.calls[-1]
    assert (method, path) == ("POST", "/api/v1/commands/repository.commits")
    assert body == {
        "resource_ref": "external_resource_repository",
        "revision": "main",
        "limit": 12,
    }


def test_repository_cli_requires_explicit_confirmation_for_side_effects(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RepositoryCommandTransport()

    code, output, error = _invoke(
        config,
        transport,
        "repository",
        "commit",
        "external_resource_repository",
        "--message",
        "Change",
        "--author-name",
        "Repository CLI",
        "--author-email",
        "repository@example.invalid",
    )
    assert code == 2 and not output
    assert "--yes" in error["message"]
    assert transport.calls == []

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "repository",
        "commit",
        "external_resource_repository",
        "--message",
        "Change",
        "--author-name",
        "Repository CLI",
        "--author-email",
        "repository@example.invalid",
        "--approval-id",
        "approval_example",
        "--idempotency-key",
        "repository-cli-commit",
    )
    assert code == 0 and not error
    method, path, _, headers, body = transport.calls[-1]
    assert (method, path) == ("POST", "/api/v1/commands/repository.commit")
    assert headers["idempotency-key"] == "repository-cli-commit"
    assert body == {
        "resource_ref": "external_resource_repository",
        "message": "Change",
        "author_name": "Repository CLI",
        "author_email": "repository@example.invalid",
        "approval_id": "approval_example",
    }


def test_repository_cli_discovery_only_requires_confirmation_when_attaching(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RepositoryCommandTransport()

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "discover",
        "connection_example",
        "--provider-id",
        "repository-github",
    )
    assert code == 0 and not error
    _, path, _, _, body = transport.calls[-1]
    assert path == "/api/v1/commands/repository.discover"
    assert body == {
        "resource_ref": "connection_example",
        "provider_id": "repository-github",
        "attach": False,
    }

    previous_call_count = len(transport.calls)
    code, output, error = _invoke(
        config,
        transport,
        "repository",
        "discover",
        "connection_example",
        "--provider-id",
        "repository-github",
        "--attach",
    )
    assert code == 2 and not output
    assert "--yes" in error["message"]
    assert len(transport.calls) == previous_call_count

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "repository",
        "discover",
        "connection_example",
        "--provider-id",
        "repository-github",
        "--attach",
    )
    assert code == 0 and not error
    assert transport.calls[-1][4]["attach"] is True
