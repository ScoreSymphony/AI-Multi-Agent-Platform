from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli


_GOAL_COMMANDS = [
    "goal.create",
    "goal.activate",
    "goal.pause",
    "goal.resume",
    "goal.cancel",
    "goal.revise",
    "goal.review",
    "goal.attach-task",
    "goal.record-task-outcome",
]


class GoalCLITransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None, Mapping[str, str]]] = []

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
        path = urlsplit(url).path
        decoded = None if body is None else json.loads(body.decode("utf-8"))
        assert decoded is None or isinstance(decoded, dict)
        self.calls.append((method, path, decoded, headers))

        if path == "/api/v1/openapi.json":
            payload: object = {
                "openapi": "3.1.0",
                "x-registered-extension-collections": ["goals"],
                "x-registered-extension-commands": _GOAL_COMMANDS,
            }
        elif path == "/api/v1/goals":
            payload = {
                "items": [{"id": "goal_cli", "type": "goal", "status": "active"}],
                "total": 1,
                "next_cursor": None,
                "limit": 50,
            }
        elif path == "/api/v1/goals/goal_cli":
            payload = {"id": "goal_cli", "type": "goal", "status": "active", "revision": 3}
        elif path.startswith("/api/v1/commands/goal."):
            payload = {"id": "goal_cli", "type": "goal", "status": "active", "revision": 3}
        else:
            raise AssertionError(f"unexpected request: {method} {path}")

        return RawResponse(
            status=200,
            body=json.dumps(payload).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: GoalCLITransport,
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


def test_goal_list_and_detail_are_available_through_cli_extension_surface(tmp_path: Path) -> None:
    transport = GoalCLITransport()
    config = tmp_path / "cli.json"

    code, listed, error = _invoke(config, transport, "extension", "list", "goals")
    assert code == 0 and not error
    assert listed["data"]["total"] == 1  # type: ignore[index]

    code, shown, error = _invoke(config, transport, "extension", "show", "goals", "goal_cli")
    assert code == 0 and not error
    assert shown["data"]["id"] == "goal_cli"  # type: ignore[index]


def test_goal_lifecycle_revision_and_task_link_commands_use_registered_control_plane_surface(
    tmp_path: Path,
) -> None:
    transport = GoalCLITransport()
    config = tmp_path / "cli.json"

    commands = (
        ("goal.pause", "goal_cli", {}),
        ("goal.resume", "goal_cli", {}),
        ("goal.cancel", "goal_cli", {"reason": "operator decision"}),
        (
            "goal.revise",
            "goal_cli",
            {
                "expected_revision": 3,
                "objective": "Revised objective",
                "active_task_policy": "retain",
            },
        ),
        (
            "goal.attach-task",
            "goal_cli",
            {"expected_revision": 4, "task_id": "task_cli"},
        ),
    )

    for index, (command, resource_ref, payload) in enumerate(commands, start=1):
        code, _, error = _invoke(
            config,
            transport,
            "extension",
            "execute",
            command,
            resource_ref,
            "--payload",
            json.dumps(payload),
            "--idempotency-key",
            f"goal-cli-{index}",
        )
        assert code == 0 and not error

    posts = [call for call in transport.calls if call[0] == "POST"]
    assert [call[1] for call in posts] == [f"/api/v1/commands/{item[0]}" for item in commands]
    assert posts[2][2] == {"resource_ref": "goal_cli", "reason": "operator decision"}
    assert posts[3][2] == {
        "resource_ref": "goal_cli",
        "expected_revision": 3,
        "objective": "Revised objective",
        "active_task_policy": "retain",
    }
    assert posts[4][2] == {
        "resource_ref": "goal_cli",
        "expected_revision": 4,
        "task_id": "task_cli",
    }
    assert all("Idempotency-Key" in call[3] for call in posts)
