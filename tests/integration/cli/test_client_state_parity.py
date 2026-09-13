from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class _ParityTransport:
    def __init__(self, payloads: Mapping[str, object]) -> None:
        self.payloads = dict(payloads)
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
        self.calls.append((method, url))
        for path, payload in self.payloads.items():
            if url.endswith(path):
                return RawResponse(
                    status=200,
                    body=json.dumps(payload).encode("utf-8"),
                    headers={"x-api-version": "v1"},
                )
        raise AssertionError(f"unexpected client-parity URL: {url}")


def _fixture(name: str) -> dict[str, object]:
    payload = json.loads((Path("frontend/src/api/__fixtures__") / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_cli_reads_shared_canonical_task_run_result_state(tmp_path: Path) -> None:
    task = _fixture("canonical-task.json")
    run = _fixture("canonical-run.json")
    result = _fixture("canonical-result.json")
    task_id = task["id"]
    run_id = run["id"]
    result_id = result["id"]
    assert isinstance(task_id, str)
    assert isinstance(run_id, str)
    assert isinstance(result_id, str)
    assert run["task_id"] == task_id
    assert result["task_id"] == task_id
    assert task["run_ids"] == [run_id]
    assert task["result_ids"] == [result_id]
    assert task["status"] == run["status"] == "succeeded"
    assert task["correlation_id"] == run["correlation_id"]

    config = tmp_path / "cli.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "current_profile": "reference",
                "profiles": {
                    "reference": {
                        "endpoint": "http://control-plane.invalid",
                        "principal_ref": "user:issue-46-client-parity",
                        "owner_type": "user",
                        "owner_id": "issue-46-client-parity",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    transport = _ParityTransport(
        {
            f"/api/v1/tasks/{task_id}": task,
            f"/api/v1/runs/{run_id}": run,
            f"/api/v1/results/{result_id}": result,
        }
    )

    commands = (
        (("task", "show", task_id), task),
        (("run", "show", run_id), run),
        (("result", "show", result_id), result),
    )
    for command, expected in commands:
        stdout = StringIO()
        stderr = StringIO()
        code = run_cli(
            ("--config", str(config), "--json", *command),
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )
        assert code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue())["data"] == expected

    assert transport.calls == [
        ("GET", f"http://control-plane.invalid/api/v1/tasks/{task_id}"),
        ("GET", f"http://control-plane.invalid/api/v1/runs/{run_id}"),
        ("GET", f"http://control-plane.invalid/api/v1/results/{result_id}"),
    ]
