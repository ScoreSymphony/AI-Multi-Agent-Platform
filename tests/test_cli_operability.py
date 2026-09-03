from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.completion import completion_script
from ai_multi_agent_platform.cli.completion import main as completion_main
from ai_multi_agent_platform.cli.main import run_cli


class RecordingTransport:
    def __init__(self) -> None:
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
        return RawResponse(
            status=200,
            body=json.dumps(
                {
                    "api_version": "v1",
                    "resources": [],
                    "commands": [],
                }
            ).encode("utf-8"),
            headers={
                "x-api-version": "v1",
                "x-request-id": "request_remote",
                "x-correlation-id": "corr_remote",
            },
        )


def _local_cli(config: Path, *arguments: str) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), *arguments],
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_remote_profile_targets_remote_control_plane_api(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    code, _, error = _local_cli(
        config,
        "profile",
        "set",
        "remote",
        "https://control.example.test/base",
    )
    assert code == 0 and not error
    code, _, error = _local_cli(config, "profile", "use", "remote")
    assert code == 0 and not error

    transport = RecordingTransport()
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", "status"],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert not stderr.getvalue()
    assert transport.urls == ["https://control.example.test/base/api/v1/"]
    payload = json.loads(stdout.getvalue())
    assert payload["meta"]["request_id"] == "request_remote"
    assert payload["meta"]["correlation_id"] == "corr_remote"


def test_completion_scripts_cover_current_command_hierarchy() -> None:
    for shell in ("bash", "zsh", "fish"):
        script = completion_script(shell)
        assert "platform" in script
        assert "status" in script
        assert "task" in script
        assert "model-provider" in script
        assert "extension" in script
        assert "refresh-health" in script


def test_completion_entrypoint_writes_requested_shell_script() -> None:
    stdout = StringIO()

    code = completion_main(["bash"], stdout=stdout)

    assert code == 0
    assert "complete -F _platform_complete platform" in stdout.getvalue()
