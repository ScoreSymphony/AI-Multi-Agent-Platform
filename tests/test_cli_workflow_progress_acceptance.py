from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class AcceptanceWorkflowTransport:
    def __init__(self, *, deny_projection: bool = False) -> None:
        self.deny_projection = deny_projection
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
            payload: dict[str, Any] = {"id": "task_421", "plan_ref": "plan_421"}
            status = 200
        elif url.endswith("/api/v1/plan-coordination/plan_421"):
            if self.deny_projection:
                payload = {
                    "code": "forbidden",
                    "category": "authorization",
                    "message": "workflow projection is outside the caller scope",
                    "retryable": False,
                    "details": {"authorization_outcome": "deny"},
                }
                status = 403
            else:
                payload = {
                    "id": "plan_421",
                    "task_id": "task_421",
                    "plan_revision": 9,
                    "steps": [
                        {
                            "id": "step_root",
                            "status": "succeeded",
                            "coordination_phase": "terminal",
                            "coordination_revision": 2,
                            "dependency_ids": [],
                            "satisfied_dependency_ids": [],
                            "latest_run_id": "run_root",
                            "current_attempt": 1,
                            "retry_due_at": None,
                            "wait_type": None,
                            "wait_deadline_at": None,
                            "reconciliation": "consistent",
                            "reconciliation_detail": None,
                            "lease_token": "lease-secret-421",
                            "backend_workflow_id": "temporal-private-421",
                            "raw_provider_payload": {"secret": "provider-secret-421"},
                        },
                        {
                            "id": "step_review",
                            "status": "waiting",
                            "coordination_phase": "waiting",
                            "coordination_revision": 4,
                            "dependency_ids": ["step_root"],
                            "satisfied_dependency_ids": ["step_root"],
                            "latest_run_id": "run_review",
                            "current_attempt": 1,
                            "retry_due_at": None,
                            "wait_type": "approval",
                            "wait_deadline_at": "2026-09-08T12:00:00+00:00",
                            "reconciliation": "consistent",
                            "reconciliation_detail": None,
                        },
                        {
                            "id": "step_join",
                            "status": "pending",
                            "coordination_phase": "blocked",
                            "coordination_revision": 3,
                            "dependency_ids": ["step_root", "step_review"],
                            "satisfied_dependency_ids": ["step_root"],
                            "latest_run_id": None,
                            "current_attempt": 0,
                            "retry_due_at": None,
                            "wait_type": None,
                            "wait_deadline_at": None,
                            "reconciliation": "consistent",
                            "reconciliation_detail": None,
                        },
                        {
                            "id": "step_retry",
                            "status": "failed",
                            "coordination_phase": "terminal",
                            "coordination_revision": 8,
                            "dependency_ids": ["step_root"],
                            "satisfied_dependency_ids": ["step_root"],
                            "latest_run_id": "run_retry",
                            "current_attempt": 3,
                            "retry_due_at": None,
                            "wait_type": None,
                            "wait_deadline_at": None,
                            "reconciliation": "run_reconciled",
                            "reconciliation_detail": "terminal Run reconciled after restart",
                        },
                        {
                            "id": "step_cancelled",
                            "status": "cancelled",
                            "coordination_phase": "terminal",
                            "coordination_revision": 5,
                            "dependency_ids": ["step_root"],
                            "satisfied_dependency_ids": ["step_root"],
                            "latest_run_id": None,
                            "current_attempt": 1,
                            "retry_due_at": None,
                            "wait_type": None,
                            "wait_deadline_at": None,
                            "reconciliation": "canonical_terminal",
                            "reconciliation_detail": None,
                        },
                    ],
                }
                status = 200
        else:
            raise AssertionError(f"unexpected Control Plane URL: {url}")
        return RawResponse(
            status=status,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def test_task_workflow_human_summary_and_steps_are_readable(tmp_path: Path) -> None:
    transport = AcceptanceWorkflowTransport()
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        ["--config", str(tmp_path / "cli.json"), "task", "workflow", "show", "task_421"],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    rendered = stdout.getvalue()
    assert "task_id: task_421" in rendered
    assert "plan_id: plan_421" in rendered
    assert "plan_revision: 9" in rendered
    assert "step_count: 5" in rendered
    assert "waiting_step_count: 1" in rendered
    assert "retry_step_count: 1" in rendered


def test_task_workflow_json_allowlists_canonical_projection_fields(tmp_path: Path) -> None:
    transport = AcceptanceWorkflowTransport()
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "task",
            "workflow",
            "steps",
            "task_421",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    payload = json.loads(stdout.getvalue())
    items = payload["data"]["items"]
    assert [item["id"] for item in items] == [
        "step_root",
        "step_review",
        "step_join",
        "step_retry",
        "step_cancelled",
    ]
    assert items[2]["dependency_ids"] == ["step_root", "step_review"]
    assert items[2]["satisfied_dependency_ids"] == ["step_root"]
    assert items[1]["wait_type"] == "approval"
    assert items[3]["current_attempt"] == 3
    assert items[3]["reconciliation"] == "run_reconciled"
    assert items[4]["status"] == "cancelled"
    serialized = json.dumps(payload, sort_keys=True)
    assert "lease-secret-421" not in serialized
    assert "temporal-private-421" not in serialized
    assert "provider-secret-421" not in serialized
    assert "lease_token" not in serialized
    assert "backend_workflow_id" not in serialized
    assert "raw_provider_payload" not in serialized


def test_task_workflow_preserves_canonical_authorization_error(tmp_path: Path) -> None:
    transport = AcceptanceWorkflowTransport(deny_projection=True)
    stdout = StringIO()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "task",
            "workflow",
            "show",
            "task_421",
        ],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 3
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["code"] == "forbidden"
    assert error["category"] == "authorization"
    assert error["status"] == 403
    assert error["details"]["authorization_outcome"] == "deny"
    assert transport.urls == [
        "http://127.0.0.1:8000/api/v1/tasks/task_421",
        "http://127.0.0.1:8000/api/v1/plan-coordination/plan_421",
    ]
