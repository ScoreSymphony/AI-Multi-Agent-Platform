from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, str], dict[str, Any]]] = []

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
        decoded: dict[str, Any] = {}
        if body is not None:
            value = json.loads(body.decode("utf-8"))
            assert isinstance(value, dict)
            decoded = value
        self.calls.append((method, url, headers, decoded))
        return RawResponse(
            status=200,
            body=b'{"ok":true}',
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
    yes: bool = False,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    argv = ["--config", str(config), "--json"]
    if yes:
        argv.append("--yes")
    argv.extend(arguments)
    code = run_cli(
        argv,
        transport=transport,
        stdout=stdout,
        stderr=stderr,
        stdin=StringIO(),
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def _last_call(transport: RecordingTransport) -> tuple[str, str, Mapping[str, str], dict[str, Any]]:
    assert transport.calls
    return transport.calls[-1]


def test_repository_cli_lists_and_shows_canonical_resources(tmp_path: Path) -> None:
    transport = RecordingTransport()
    config = tmp_path / "cli.json"

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "list",
        "--filter",
        "connection_id=connection_123",
        "--q",
        "score",
    )
    assert code == 0 and not error
    method, url, _, body = _last_call(transport)
    parsed = urlsplit(url)
    assert method == "GET"
    assert parsed.path == "/api/v1/repositories"
    query = dict(parse_qsl(parsed.query))
    assert query["filter[connection_id]"] == "connection_123"
    assert query["q"] == "score"
    assert body == {}

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "show",
        "external_resource_with/slash",
    )
    assert code == 0 and not error
    assert urlsplit(_last_call(transport)[1]).path.endswith(
        "/repositories/external_resource_with%2Fslash"
    )


def test_repository_cli_management_routes_only_through_canonical_commands(tmp_path: Path) -> None:
    transport = RecordingTransport()
    config = tmp_path / "cli.json"

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "discover",
        "connection_123",
        "--provider-id",
        "repository-github",
    )
    assert code == 0 and not error
    method, url, headers, body = _last_call(transport)
    assert method == "POST"
    assert urlsplit(url).path == "/api/v1/commands/repository.discover"
    assert body == {
        "resource_ref": "connection_123",
        "provider_id": "repository-github",
        "attach": False,
    }
    assert headers["idempotency-key"].startswith("cli_")

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "attach-local",
        "project_123",
        "--name",
        "music-analysis",
        "--initialize",
        yes=True,
    )
    assert code == 0 and not error
    _, url, _, body = _last_call(transport)
    assert urlsplit(url).path == "/api/v1/commands/repository.local.attach"
    assert body == {
        "resource_ref": "project_123",
        "name": "music-analysis",
        "initialize": True,
        "default_branch": "main",
    }

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "detach",
        "external_resource_123",
        yes=True,
    )
    assert code == 0 and not error
    _, url, _, body = _last_call(transport)
    assert urlsplit(url).path == "/api/v1/commands/repository.detach"
    assert body == {"resource_ref": "external_resource_123"}


def test_repository_cli_git_mutations_require_confirmation_and_preserve_exact_arguments(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport()
    config = tmp_path / "cli.json"

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "push",
        "external_resource_123",
        "--refspec",
        "refs/heads/feature:refs/heads/feature",
    )
    assert code == 2
    assert "requires confirmation" in error
    assert transport.calls == []

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "commit",
        "external_resource_123",
        "--message",
        "record exact revision",
        "--author-name",
        "Platform Agent",
        "--author-email",
        "agent@example.invalid",
        "--idempotency-key",
        "issue-82-commit",
        yes=True,
    )
    assert code == 0 and not error
    _, url, headers, body = _last_call(transport)
    assert urlsplit(url).path == "/api/v1/commands/repository.commit"
    assert headers["idempotency-key"] == "issue-82-commit"
    assert body == {
        "resource_ref": "external_resource_123",
        "message": "record exact revision",
        "author_name": "Platform Agent",
        "author_email": "agent@example.invalid",
    }


def test_repository_cli_collaboration_uses_canonical_reference_json(tmp_path: Path) -> None:
    transport = RecordingTransport()
    config = tmp_path / "cli.json"
    resource = {
        "id": "external_resource_issue_123",
        "connection_id": "connection_123",
        "resource_type": "repository_issue",
        "native_reference": {"namespace": "github", "value": "42"},
    }

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "issue",
        "show",
        "external_resource_repo_123",
        "--resource-json",
        json.dumps(resource),
    )
    assert code == 0 and not error
    _, url, _, body = _last_call(transport)
    assert urlsplit(url).path == "/api/v1/commands/repository.issue.read"
    assert body == {
        "resource_ref": "external_resource_repo_123",
        "resource": resource,
    }

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "change-request",
        "open",
        "external_resource_repo_123",
        "--title",
        "Feature",
        "--head-ref",
        "feature",
        "--base-ref",
        "main",
        yes=True,
    )
    assert code == 0 and not error
    _, url, _, body = _last_call(transport)
    assert urlsplit(url).path == "/api/v1/commands/repository.change_request.open"
    assert body == {
        "resource_ref": "external_resource_repo_123",
        "title": "Feature",
        "head_ref": "feature",
        "base_ref": "main",
    }
