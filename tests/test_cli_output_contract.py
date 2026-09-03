from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


class CaptureTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        self.calls.append((method, url, dict(headers), body, timeout))
        return RawResponse(
            status=200,
            body=json.dumps(
                {
                    "api_version": "v1",
                    "resources": ["tasks", "runs"],
                }
            ).encode("utf-8"),
            headers={
                "x-api-version": "v1",
                "x-request-id": "request_remote_contract",
                "x-correlation-id": "corr_remote_contract",
            },
        )


def _remote_config(path: Path) -> None:
    store = ProfileStore.load(path)
    store.set_profile(
        "remote",
        CLIProfile(
            endpoint="https://control.example.test/platform-root/",
            principal_ref="user:remote-operator",
            owner_type="team",
            owner_id="team-operations",
        ),
    )
    store.use("remote")
    store.save()


def test_remote_profile_targets_versioned_control_plane_and_propagates_context(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    _remote_config(config)
    transport = CaptureTransport()
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
    assert len(transport.calls) == 1
    method, url, headers, body, timeout = transport.calls[0]
    assert method == "GET"
    assert url == "https://control.example.test/platform-root/api/v1/"
    assert body is None
    assert timeout == 10.0
    assert headers["accept"] == "application/json"
    assert headers["x-principal-ref"] == "user:remote-operator"
    assert headers["x-owner-type"] == "team"
    assert headers["x-owner-id"] == "team-operations"
    assert headers["x-request-id"].startswith("request_")
    assert headers["x-correlation-id"].startswith("corr_")


def test_remote_success_json_envelope_is_stable_and_uses_server_diagnostic_ids(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    _remote_config(config)
    transport = CaptureTransport()
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
    assert stdout.getvalue() == (
        '{"data":{"api_version":"v1","resources":["tasks","runs"]},'
        '"meta":{"api_version":"v1","correlation_id":"corr_remote_contract",'
        '"request_id":"request_remote_contract","status":200}}\n'
    )

    payload = json.loads(stdout.getvalue())
    assert set(payload) == {"data", "meta"}
    assert set(payload["meta"]) == {
        "api_version",
        "correlation_id",
        "request_id",
        "status",
    }
