from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class WorkflowTransport:
    def __init__(self, *, plan_ref: str | None = "plan_421") -> None:
        self.plan_ref = plan_ref
        self.urls: list[str] = []

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
        if url.endswith("/api/v1/tasks/task_421"):
            payload: dict[str, Any] = {"id": "task_421", "plan_ref": self.plan_ref}
        elif url.endswith("/api/v1/plan-coordination/plan_421"):
            payload = {
                "id": "plan_421",
                "task_id": "task_421",
                "plan_revision": 7,
                "steps": [
                    {
                        "id": "step_a",
                        "status": "succeeded",
                        "coordination_phase": "terminal",
                        "coordination_revision": 2,
                        "dependency_ids": [],
                        "satisfied_dependency_ids": [],
                        "latest_run_id": "run_a",
                        "current_attempt": 1,
                        "retry_due_at": None,
                        "wait_type": None,
                        "wait_deadline_at": None,
                        "reconciliation": "clean",
                        "reconciliation_detail": None,
                    },
                    {
                        "id": "step_b",
                        "status": "waiting",
                        "coordination_phase": "waiting",
                        "coordination_revision": 4,
                        "dependency_ids": ["step_a"],
                        "satisfied_dependency_ids": ["step_a"],
                        "latest_run_id": "run_b",
                        "current_attempt": 1,
                        "retry_due_at": None,
                        "wait_type": "external_signal",
                        "wait_deadline_at": "2026-09-08T12:00:00+00:00",
                        "reconciliation": "clean",
                        "reconciliation_detail": None,
                    },
                    {
                        "id": "step_c",
                        "status": "waiting",
                        "coordination_phase": "retry_wait",
                        "coordination_revision": 5,
                        "dependency_ids": ["step_a"],
                        "satisfied_dependency_ids": ["step_a"],
                        "latest_run_id": "run_c",
                        "current_attempt": 2,
                        "retry_due_at": "2026-09-08T12:05:00+00:00",
                        "wait_type": None,
                        "wait_deadline_at": None,
                        "reconciliation": "clean",
                        "reconciliation_detail": None,
                    },
                    {
                        "id": "step_d",
                        "status": "failed",
                        "coordination_phase": "terminal",
                        "coordination_revision": 8,
                        "dependency_ids": ["step_b", "step_c"],
                        "satisfied_dependency_ids": ["step_b", "step_c"],
                        "latest_run_id": "run_d",
                        "current_attempt": 3,
                        "retry_due_at": None,
                        "wait_type": None,
                        "wait_deadline_at": None,
                        "reconciliation": "repaired",
                        "reconciliation_detail": "operator repair applied",
                    },
                ],
            }
        else:
            raise AssertionError(f"unexpected Control Plane URL: {url}")
        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: WorkflowTransport,
    view: str,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        [
            "--config",
            str(config),
            "--json",
            "task",
            "workflow",
            view,
            "task_421",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def test_task_workflow_views_consume_only_the_versioned_control_plane(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"

    for view in ("show", "steps", "waits", "retries"):
        transport = WorkflowTransport()
        code, payload, error = _invoke(config, transport, view)
        assert code == 0
        assert error == ""
        assert transport.urls == [
            "http://127.0.0.1:8000/api/v1/tasks/task_421",
            "http://127.0.0.1:8000/api/v1/plan-coordination/plan_421",
        ]
        assert payload["meta"]["api_version"] == "v1"

        data = payload["data"]
        assert data["task_id"] == "task_421"
        assert data["plan_id"] == "plan_421"
        assert data["plan_revision"] == 7
        if view == "show":
            assert data["step_count"] == 4
            assert data["status_counts"] == {"failed": 1, "succeeded": 1, "waiting": 2}
            assert data["waiting_step_count"] == 1
            assert data["retry_step_count"] == 2
        elif view == "steps":
            assert [item["id"] for item in data["items"]] == [
                "step_a",
                "step_b",
                "step_c",
                "step_d",
            ]
        elif view == "waits":
            assert [item["id"] for item in data["items"]] == ["step_b"]
        else:
            assert [item["id"] for item in data["items"]] == ["step_c", "step_d"]


def test_task_workflow_without_plan_is_a_normal_empty_projection(tmp_path: Path) -> None:
    transport = WorkflowTransport(plan_ref=None)
    code, payload, error = _invoke(tmp_path / "cli.json", transport, "steps")

    assert code == 0
    assert error == ""
    assert transport.urls == ["http://127.0.0.1:8000/api/v1/tasks/task_421"]
    assert payload["data"] == {
        "items": [],
        "plan_id": None,
        "plan_revision": None,
        "task_id": "task_421",
        "total": 0,
    }
