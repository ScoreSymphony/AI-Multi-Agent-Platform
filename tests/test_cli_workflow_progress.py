from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


class WorkflowTransport:
    def __init__(
        self,
        *,
        plan_ref: str | None = "plan_421",
        missing_projection: bool = False,
        forbidden_projection: bool = False,
    ) -> None:
        self.plan_ref = plan_ref
        self.missing_projection = missing_projection
        self.forbidden_projection = forbidden_projection
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
        status = 200
        if url.endswith("/api/v1/tasks/task_421"):
            payload: dict[str, Any] = {"id": "task_421", "plan_ref": self.plan_ref}
        elif url.endswith("/api/v1/plan-coordination/plan_421"):
            if self.forbidden_projection:
                status = 403
                payload = {
                    "code": "forbidden",
                    "category": "authorization",
                    "message": "workflow scope is not authorized",
                    "retryable": False,
                }
            elif self.missing_projection:
                status = 404
                payload = {
                    "code": "not_found",
                    "category": "resource",
                    "message": "plan coordination projection not found",
                    "retryable": False,
                }
            else:
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
                            "retry_state": "none",
                            "retry_max_attempts": 1,
                            "wait_key": None,
                            "wait_type": None,
                            "wait_state": None,
                            "wait_deadline_at": None,
                            "wait_resolved_at": None,
                            "wait_approval_id": None,
                            "wait_approval_subject_type": None,
                            "wait_approval_subject_id": None,
                            "wait_approval_action": None,
                            "wait_event_type": None,
                            "wait_correlation_key": None,
                            "wait_external_job_ref": None,
                            "reconciliation": "consistent",
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
                            "retry_state": "none",
                            "retry_max_attempts": 1,
                            "wait_key": "wait-external-421",
                            "wait_type": "external_job",
                            "wait_state": "active",
                            "wait_deadline_at": "2026-09-08T12:00:00+00:00",
                            "wait_resolved_at": None,
                            "wait_approval_id": None,
                            "wait_approval_subject_type": None,
                            "wait_approval_subject_id": None,
                            "wait_approval_action": None,
                            "wait_event_type": None,
                            "wait_correlation_key": None,
                            "wait_external_job_ref": "adapter-job-421",
                            "reconciliation": "consistent",
                            "reconciliation_detail": None,
                            "lease_token": "must-not-reach-cli",
                        },
                        {
                            "id": "step_c",
                            "status": "failed",
                            "coordination_phase": "retry_scheduled",
                            "coordination_revision": 5,
                            "dependency_ids": ["step_a"],
                            "satisfied_dependency_ids": ["step_a"],
                            "latest_run_id": "run_c",
                            "current_attempt": 1,
                            "retry_due_at": "2026-09-08T12:05:00+00:00",
                            "retry_state": "scheduled",
                            "retry_max_attempts": 2,
                            "wait_key": None,
                            "wait_type": None,
                            "wait_state": None,
                            "wait_deadline_at": None,
                            "wait_resolved_at": None,
                            "wait_approval_id": None,
                            "wait_approval_subject_type": None,
                            "wait_approval_subject_id": None,
                            "wait_approval_action": None,
                            "wait_event_type": None,
                            "wait_correlation_key": None,
                            "wait_external_job_ref": None,
                            "reconciliation": "consistent",
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
                            "current_attempt": 2,
                            "retry_due_at": None,
                            "retry_state": "exhausted",
                            "retry_max_attempts": 2,
                            "wait_key": None,
                            "wait_type": None,
                            "wait_state": None,
                            "wait_deadline_at": None,
                            "wait_resolved_at": None,
                            "wait_approval_id": None,
                            "wait_approval_subject_type": None,
                            "wait_approval_subject_id": None,
                            "wait_approval_action": None,
                            "wait_event_type": None,
                            "wait_correlation_key": None,
                            "wait_external_job_ref": None,
                            "reconciliation": "run_reconciled",
                            "reconciliation_detail": "canonical Run reconciled after restart",
                            "raw_provider_payload": "must-not-reach-cli",
                        },
                        {
                            "id": "step_e",
                            "status": "failed",
                            "coordination_phase": "terminal",
                            "coordination_revision": 3,
                            "dependency_ids": [],
                            "satisfied_dependency_ids": [],
                            "latest_run_id": "run_e",
                            "current_attempt": 1,
                            "retry_due_at": None,
                            "retry_state": "not_retryable",
                            "retry_max_attempts": 3,
                            "wait_key": None,
                            "wait_type": None,
                            "wait_state": None,
                            "wait_deadline_at": None,
                            "wait_resolved_at": None,
                            "wait_approval_id": None,
                            "wait_approval_subject_type": None,
                            "wait_approval_subject_id": None,
                            "wait_approval_action": None,
                            "wait_event_type": None,
                            "wait_correlation_key": None,
                            "wait_external_job_ref": None,
                            "reconciliation": "consistent",
                            "reconciliation_detail": None,
                        },
                        {
                            "id": "step_f",
                            "status": "failed",
                            "coordination_phase": "terminal",
                            "coordination_revision": 6,
                            "dependency_ids": [],
                            "satisfied_dependency_ids": [],
                            "latest_run_id": "run_f",
                            "current_attempt": 1,
                            "retry_due_at": None,
                            "retry_state": "none",
                            "retry_max_attempts": 1,
                            "wait_key": "wait-approval-421",
                            "wait_type": "approval",
                            "wait_state": "expired",
                            "wait_deadline_at": "2026-09-08T13:00:00+00:00",
                            "wait_resolved_at": "2026-09-08T13:00:01+00:00",
                            "wait_approval_id": "approval_421",
                            "wait_approval_subject_type": "step",
                            "wait_approval_subject_id": "step_f",
                            "wait_approval_action": "execute",
                            "wait_event_type": None,
                            "wait_correlation_key": None,
                            "wait_external_job_ref": None,
                            "reconciliation": "consistent",
                            "reconciliation_detail": None,
                        },
                    ],
                }
        else:
            raise AssertionError(f"unexpected Control Plane URL: {url}")
        return RawResponse(
            status=status,
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
            assert data["step_count"] == 6
            assert data["status_counts"] == {"failed": 4, "succeeded": 1, "waiting": 1}
            assert data["waiting_step_count"] == 1
            assert data["retry_step_count"] == 3
        elif view == "steps":
            assert [item["id"] for item in data["items"]] == [
                "step_a",
                "step_b",
                "step_c",
                "step_d",
                "step_e",
                "step_f",
            ]
            serialized = json.dumps(data, sort_keys=True)
            assert "must-not-reach-cli" not in serialized
            assert "lease_token" not in serialized
            assert "raw_provider_payload" not in serialized
        elif view == "waits":
            assert [item["id"] for item in data["items"]] == ["step_b", "step_f"]
            active, expired = data["items"]
            assert active["wait_state"] == "active"
            assert active["wait_external_job_ref"] == "adapter-job-421"
            assert expired["wait_state"] == "expired"
            assert expired["wait_approval_id"] == "approval_421"
            assert expired["wait_approval_action"] == "execute"
            assert expired["wait_resolved_at"] == "2026-09-08T13:00:01+00:00"
        else:
            assert [item["id"] for item in data["items"]] == ["step_c", "step_d", "step_e"]
            assert [item["retry_state"] for item in data["items"]] == [
                "scheduled",
                "exhausted",
                "not_retryable",
            ]
            assert data["items"][0]["retry_due_at"] == "2026-09-08T12:05:00+00:00"
            assert data["items"][1]["retry_max_attempts"] == 2
            assert data["items"][2]["current_attempt"] == 1


def test_task_workflow_human_output_identifies_task_plan_revision_and_retry_state(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "task",
            "workflow",
            "retries",
            "task_421",
        ],
        transport=WorkflowTransport(),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert stderr.getvalue() == ""
    rendered = stdout.getvalue()
    assert "task_id: task_421" in rendered
    assert "plan_id: plan_421" in rendered
    assert "plan_revision: 7" in rendered
    assert "retry_state" in rendered
    assert "scheduled" in rendered
    assert "exhausted" in rendered
    assert "not_retryable" in rendered


def test_task_workflow_authorization_error_remains_canonical_and_reveals_no_projection(
    tmp_path: Path,
) -> None:
    transport = WorkflowTransport(forbidden_projection=True)
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        [
            "--config",
            str(tmp_path / "cli.json"),
            "--json",
            "task",
            "workflow",
            "waits",
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
    assert "approval_421" not in stderr.getvalue()
    assert "adapter-job-421" not in stderr.getvalue()


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


def test_task_workflow_with_plan_but_no_coordination_projection_is_normal_empty_state(
    tmp_path: Path,
) -> None:
    transport = WorkflowTransport(missing_projection=True)
    code, payload, error = _invoke(tmp_path / "cli.json", transport, "steps")

    assert code == 0
    assert error == ""
    assert transport.urls == [
        "http://127.0.0.1:8000/api/v1/tasks/task_421",
        "http://127.0.0.1:8000/api/v1/plan-coordination/plan_421",
    ]
    assert payload["data"] == {
        "items": [],
        "plan_id": "plan_421",
        "plan_revision": None,
        "task_id": "task_421",
        "total": 0,
    }
