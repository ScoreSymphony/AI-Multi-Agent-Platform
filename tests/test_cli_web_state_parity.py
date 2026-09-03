from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "frontend"
    / "src"
    / "api"
    / "__fixtures__"
    / "canonical-task.json"
)


class SharedTaskFixtureTransport:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
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
        expected = f"/api/v1/tasks/{self.task['id']}"
        if method != "GET" or path != expected:
            return RawResponse(
                status=404,
                body=json.dumps(
                    {
                        "code": "not_found",
                        "category": "resource",
                        "message": "fixture route not found",
                        "request_id": "request_shared_client_state",
                        "correlation_id": "corr_shared_client_state",
                        "retryable": False,
                    }
                ).encode("utf-8"),
                headers={},
            )
        return RawResponse(
            status=200,
            body=json.dumps(self.task).encode("utf-8"),
            headers={
                "x-api-version": "v1",
                "x-request-id": "request_shared_client_state",
                "x-correlation-id": "corr_shared_client_state",
            },
        )


def test_cli_reads_the_same_canonical_task_snapshot_as_the_web_client(tmp_path: Path) -> None:
    loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    task = loaded
    transport = SharedTaskFixtureTransport(task)
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "task",
            "show",
            str(task["id"]),
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    rendered = json.loads(stdout.getvalue())
    assert rendered["data"] == task
    assert transport.calls == [("GET", f"/api/v1/tasks/{task['id']}")]
