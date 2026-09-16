from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_multi_agent_platform.cli.app import run_cli
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


class _RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, list[str]]]] = []

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
        parsed = urlsplit(url)
        self.calls.append((method, parsed.path, parse_qs(parsed.query)))
        payload = {
            "items": [],
            "next_cursor": None,
            "total": 0,
            "limit": 100,
            "telemetry_state": "available",
            "missing_sources": [],
            "root_ids": [],
            "task_usage": [],
            "has_hierarchy": False,
        }
        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "cli.json"
    store = ProfileStore.load(path)
    store.set_profile(
        "local",
        CLIProfile(endpoint="http://control.test", principal_ref="user:test"),
    )
    store.use("local")
    store.save()
    return path


def test_trace_cli_uses_canonical_control_plane_with_filters(tmp_path: Path) -> None:
    transport = _RecordingTransport()
    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "--json",
            "trace",
            "task_901",
            "--agent",
            "agent_901",
            "--step",
            "step_901",
            "--model-config",
            "model_901",
            "--model-provider",
            "provider_901",
            "--capability",
            "capability_shell",
            "--failure-component",
            "execution",
            "--started-after",
            "2026-09-15T20:00:00+00:00",
            "--started-before",
            "2026-09-15T21:00:00+00:00",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    assert len(transport.calls) == 1
    method, path, query = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/tasks/task_901/trace"
    assert query["filter[agent_id]"] == ["agent_901"]
    assert query["filter[step_id]"] == ["step_901"]
    assert query["filter[model_config_id]"] == ["model_901"]
    assert query["filter[model_provider_id]"] == ["provider_901"]
    assert query["filter[capability_id]"] == ["capability_shell"]
    assert query["filter[failure_component]"] == ["execution"]
    assert query["filter[started_after]"] == ["2026-09-15T20:00:00+00:00"]
    assert query["filter[started_before]"] == ["2026-09-15T21:00:00+00:00"]
    assert query["sort"] == ["timestamp"]
    assert query["direction"] == ["asc"]


def test_trace_cli_node_detail_uses_same_control_plane_surface(tmp_path: Path) -> None:
    transport = _RecordingTransport()
    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "trace",
            "task_901",
            "--node",
            "span_901",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    assert transport.calls[0][0] == "GET"
    assert transport.calls[0][1] == "/api/v1/tasks/task_901/trace/span_901"
