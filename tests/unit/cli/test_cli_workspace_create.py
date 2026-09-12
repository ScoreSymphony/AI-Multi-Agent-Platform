from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import LocalWorkspaceProvider


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

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
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append((method, parsed.path, decoded))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _transport(tmp_path: Path) -> RecordingTransport:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    files = LocalFileProvider(tmp_path / "files", tmp_path / "data.sqlite3")
    workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        workspace_provider=workspaces,
    )
    return RecordingTransport(ControlPlaneHTTP(control_plane))


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
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


def _create_project(config: Path, transport: RecordingTransport) -> str:
    code, payload, error = _invoke(
        config,
        transport,
        "project",
        "create",
        "--name",
        "CLI workspace project",
        "--owner-type",
        "user",
        "--owner-id",
        "workspace-cli-test",
    )
    assert code == 0 and not error
    project = payload["data"]
    assert isinstance(project, dict)
    project_id = project["id"]
    assert isinstance(project_id, str)
    return project_id


def test_workspace_create_forwards_rich_canonical_options(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = _transport(tmp_path)
    project_id = _create_project(config, transport)
    workspace_id = new_id("workspace")
    source_refs = [
        {
            "kind": "empty",
            "ref": "blank-source",
            "metadata": {"purpose": "cli-contract-test"},
        }
    ]

    code, payload, error = _invoke(
        config,
        transport,
        "workspace",
        "create",
        "--project-id",
        project_id,
        "--workspace-type",
        "read_only_source",
        "--access-mode",
        "read_only",
        "--retention",
        "persistent",
        "--workspace-id",
        workspace_id,
        "--source-refs-json",
        json.dumps(source_refs, separators=(",", ":")),
        "--files-json",
        "[]",
        "--idempotency-key",
        "cli-workspace-rich-create",
    )

    assert code == 0 and not error
    workspace = payload["data"]
    assert workspace["id"] == workspace_id
    assert workspace["project_id"] == project_id
    assert workspace["lifecycle"] == "canonical"
    assert workspace["workspace_type"] == "read_only_source"
    assert workspace["access_mode"] == "read_only"
    assert workspace["retention"] == "persistent"
    assert workspace["source_refs"] == [
        {
            "kind": "empty",
            "ref": "blank-source",
            "revision": None,
            "checksum": None,
            "metadata": {"purpose": "cli-contract-test"},
        }
    ]
    assert isinstance(workspace["base_snapshot_id"], str)

    method, path, body = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/workspaces"
    assert body == {
        "project_id": project_id,
        "workspace_type": "read_only_source",
        "access_mode": "read_only",
        "retention": "persistent",
        "workspace_id": workspace_id,
        "source_refs": source_refs,
        "files": [],
    }

    show_code, shown, show_error = _invoke(
        config,
        transport,
        "workspace",
        "show",
        workspace_id,
    )
    assert show_code == 0 and not show_error
    assert shown["data"]["id"] == workspace_id
    assert shown["data"]["base_snapshot_id"] == workspace["base_snapshot_id"]


def test_workspace_create_leaves_defaults_authoritative_to_control_plane(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = _transport(tmp_path)
    project_id = _create_project(config, transport)

    code, payload, error = _invoke(
        config,
        transport,
        "workspace",
        "create",
        "--project-id",
        project_id,
        "--workspace-type",
        "ephemeral_task",
    )

    assert code == 0 and not error
    workspace = payload["data"]
    assert workspace["workspace_type"] == "ephemeral_task"
    assert workspace["access_mode"] == "read_write"
    assert workspace["retention"] == "ephemeral"
    assert transport.calls[-1][2] == {
        "project_id": project_id,
        "workspace_type": "ephemeral_task",
    }


def test_workspace_json_shape_errors_are_local(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = _transport(tmp_path)
    project_id = _create_project(config, transport)
    calls_before = len(transport.calls)

    code, payload, error = _invoke(
        config,
        transport,
        "workspace",
        "create",
        "--project-id",
        project_id,
        "--source-refs-json",
        "{}",
    )

    assert code == 2
    assert payload == {}
    assert "--source-refs-json must be a JSON array" in error
    assert len(transport.calls) == calls_before
