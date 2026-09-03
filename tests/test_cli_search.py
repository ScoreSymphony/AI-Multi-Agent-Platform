from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class RecordingTransport:
    def __init__(self, response: RawResponse | None = None) -> None:
        self.urls: list[str] = []
        self.response = response or RawResponse(
            status=200,
            body=json.dumps(
                {"items": [], "total": 0, "limit": 50, "next_cursor": None}
            ).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )

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
        assert method == "GET"
        self.urls.append(url)
        return self.response


def _invoke(
    config: Path,
    transport: RecordingTransport,
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


def test_cli_search_forwards_canonical_filters_to_one_search_endpoint(tmp_path: Path) -> None:
    transport = RecordingTransport()
    code, payload, error = _invoke(
        tmp_path / "cli.json",
        transport,
        "search",
        "global search",
        "--type",
        "task,run",
        "--type",
        "project",
        "--project-id",
        "project_123",
        "--workspace-id",
        "workspace_456",
        "--status",
        "running,succeeded",
        "--tag",
        "search",
        "--source",
        "canonical",
        "--provider",
        "control-plane",
        "--updated-after",
        "2026-09-03T10:00:00+02:00",
        "--updated-before",
        "2026-09-03T18:00:00+02:00",
        "--mode",
        "keyword",
        "--limit",
        "25",
        "--cursor",
        "cursor_1",
        "--sort",
        "relevance",
        "--direction",
        "desc",
    )

    assert code == 0
    assert not error
    assert payload["data"] == {"items": [], "total": 0, "limit": 50, "next_cursor": None}
    assert len(transport.urls) == 1
    parsed = urlsplit(transport.urls[0])
    assert parsed.path == "/api/v1/search"
    query = parse_qs(parsed.query)
    assert query["q"] == ["global search"]
    assert query["type"] == ["task,run,project"]
    assert query["project_id"] == ["project_123"]
    assert query["workspace_id"] == ["workspace_456"]
    assert query["status"] == ["running,succeeded"]
    assert query["tag"] == ["search"]
    assert query["source"] == ["canonical"]
    assert query["provider"] == ["control-plane"]
    assert query["updated_after"] == ["2026-09-03T10:00:00+02:00"]
    assert query["updated_before"] == ["2026-09-03T18:00:00+02:00"]
    assert query["mode"] == ["keyword"]
    assert query["limit"] == ["25"]
    assert query["cursor"] == ["cursor_1"]
    assert query["sort"] == ["relevance"]
    assert query["direction"] == ["desc"]


def test_cli_search_supports_exact_lookup_and_surfaces_optional_mode_errors(tmp_path: Path) -> None:
    exact_transport = RecordingTransport()
    code, _, _ = _invoke(
        tmp_path / "exact.json",
        exact_transport,
        "search",
        "--id",
        "task_123",
        "--type",
        "task",
    )
    assert code == 0
    parsed = urlsplit(exact_transport.urls[0])
    query = parse_qs(parsed.query)
    assert parsed.path == "/api/v1/search"
    assert query["id"] == ["task_123"]
    assert query["type"] == ["task"]
    assert "q" not in query
    assert "mode" not in query

    unsupported = RawResponse(
        status=400,
        body=json.dumps(
            {
                "code": "unsupported_capability",
                "category": "contract",
                "message": "local search does not support semantic mode",
                "retryable": False,
            }
        ).encode("utf-8"),
        headers={"x-api-version": "v1"},
    )
    semantic_transport = RecordingTransport(unsupported)
    code, _, error = _invoke(
        tmp_path / "semantic.json",
        semantic_transport,
        "search",
        "meaning",
        "--mode",
        "semantic",
    )
    assert code == 3
    assert "unsupported_capability" in error
    assert len(semantic_transport.urls) == 1
