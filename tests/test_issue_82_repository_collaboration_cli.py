from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.issue_82 import run_cli
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


class _RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []

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
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append(
            (
                method,
                urlsplit(url).path,
                {key.casefold(): value for key, value in headers.items()},
                decoded,
            )
        )
        return RawResponse(
            status=200,
            body=b'{"ok":true}',
            headers={"x-api-version": "v1"},
        )


def _profile(config: Path) -> None:
    store = ProfileStore.load(config)
    store.set_profile(
        "local",
        CLIProfile(
            endpoint="http://control.local",
            principal_ref="user:repository-collaboration-cli",
            owner_type="user",
            owner_id="repository-collaboration-cli",
        ),
    )
    store.use("local")
    store.save()


def _invoke(
    config: Path,
    transport: _RecordingTransport,
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


def _issue_reference() -> dict[str, Any]:
    return {
        "id": "external_resource_issue_example",
        "connection_id": "connection_example",
        "resource_type": "repository_issue",
        "native_reference": {"namespace": "github", "value": "42"},
    }


def _change_request_reference() -> dict[str, Any]:
    return {
        "id": "external_resource_change_example",
        "connection_id": "connection_example",
        "resource_type": "repository_change_request",
        "native_reference": {"namespace": "gitlab", "value": "17"},
    }


def test_repository_cli_reads_collaboration_resources_by_canonical_reference_json(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RecordingTransport()
    issue = _issue_reference()

    code, _, error = _invoke(
        config,
        transport,
        "repository",
        "issue",
        "show",
        "external_resource_repository",
        "--resource-json",
        json.dumps(issue),
        "--approval-id",
        "approval_example",
    )

    assert code == 0 and not error
    method, path, headers, body = transport.calls[-1]
    assert (method, path) == ("POST", "/api/v1/commands/repository.issue.read")
    assert headers["x-principal-ref"] == "user:repository-collaboration-cli"
    assert body == {
        "resource_ref": "external_resource_repository",
        "resource": issue,
        "approval_id": "approval_example",
    }


def test_repository_cli_requires_confirmation_for_issue_writes(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RecordingTransport()

    code, output, error = _invoke(
        config,
        transport,
        "repository",
        "issue",
        "open",
        "external_resource_repository",
        "--title",
        "Repository integration gap",
    )
    assert code == 2 and not output
    assert "--yes" in error["message"]
    assert transport.calls == []

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "repository",
        "issue",
        "open",
        "external_resource_repository",
        "--title",
        "Repository integration gap",
        "--body",
        "Canonical provider-neutral body",
        "--approval-id",
        "approval_example",
        "--idempotency-key",
        "issue-82-open-issue",
    )
    assert code == 0 and not error
    method, path, headers, body = transport.calls[-1]
    assert (method, path) == ("POST", "/api/v1/commands/repository.issue.open")
    assert headers["idempotency-key"] == "issue-82-open-issue"
    assert body == {
        "resource_ref": "external_resource_repository",
        "title": "Repository integration gap",
        "body": "Canonical provider-neutral body",
        "approval_id": "approval_example",
    }


def test_repository_cli_updates_issue_without_exposing_provider_specific_command_shape(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RecordingTransport()
    issue = _issue_reference()

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "repository",
        "issue",
        "update",
        "external_resource_repository",
        "--resource-json",
        json.dumps(issue),
        "--state",
        "closed",
    )

    assert code == 0 and not error
    _, path, _, body = transport.calls[-1]
    assert path == "/api/v1/commands/repository.issue.update"
    assert body == {
        "resource_ref": "external_resource_repository",
        "resource": issue,
        "state": "closed",
    }
    assert "issue_number" not in body
    assert "project_path" not in body


def test_repository_cli_change_request_open_and_update_use_canonical_commands(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RecordingTransport()
    change_request = _change_request_reference()

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "repository",
        "change-request",
        "open",
        "external_resource_repository",
        "--title",
        "Feature branch",
        "--head-ref",
        "feature/repository",
        "--base-ref",
        "main",
    )
    assert code == 0 and not error
    _, path, _, body = transport.calls[-1]
    assert path == "/api/v1/commands/repository.change_request.open"
    assert body == {
        "resource_ref": "external_resource_repository",
        "title": "Feature branch",
        "head_ref": "feature/repository",
        "base_ref": "main",
    }

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "repository",
        "change-request",
        "update",
        "external_resource_repository",
        "--resource-json",
        json.dumps(change_request),
        "--state",
        "merged",
    )
    assert code == 0 and not error
    _, path, _, body = transport.calls[-1]
    assert path == "/api/v1/commands/repository.change_request.update"
    assert body == {
        "resource_ref": "external_resource_repository",
        "resource": change_request,
        "state": "merged",
    }


def test_repository_cli_rejects_invalid_collaboration_reference_json_before_transport(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    _profile(config)
    transport = _RecordingTransport()

    code, output, error = _invoke(
        config,
        transport,
        "repository",
        "change-request",
        "show",
        "external_resource_repository",
        "--resource-json",
        "not-json",
    )

    assert code == 2 and not output
    assert "valid JSON" in error["message"]
    assert transport.calls == []
