from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.issue_81 import run_cli
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], object]] = []

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
        decoded: object = None
        if body is not None:
            decoded = json.loads(body.decode("utf-8"))
        self.calls.append((method, urlsplit(url).path, dict(headers), decoded))
        return RawResponse(
            status=200,
            body=b'{"data":{"status":"ok"}}',
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


def test_registry_preview_is_reachable_through_platform_entrypoint(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()
    stdout = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "--json",
            "registry",
            "preview",
            "example.asset",
            "1.2.3",
        ],
        transport=transport,
        stdout=stdout,
    )

    assert code == 0
    assert transport.calls == [
        (
            "POST",
            "/commands/registry.preview",
            {"content-type": "application/json", "x-principal-ref": "user:test"},
            {"resource_ref": "example.asset", "version": "1.2.3"},
        )
    ]


def test_registry_activation_requires_global_yes_before_transport(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()
    stderr = StringIO()

    code = run_cli(
        ["--config", str(config), "registry", "activate", "example.asset", "1.2.3"],
        transport=transport,
        stderr=stderr,
    )

    assert code == 2
    assert transport.calls == []
    assert "--yes" in stderr.getvalue()


def test_registry_activation_with_yes_dispatches_exact_version(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()

    code = run_cli(
        [
            "--config",
            str(config),
            "--yes",
            "registry",
            "activate",
            "example.asset",
            "1.2.3",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    assert len(transport.calls) == 1
    method, path, _, body = transport.calls[0]
    assert method == "POST"
    assert path == "/commands/registry.activate"
    assert body == {"resource_ref": "example.asset", "version": "1.2.3"}


def test_non_registry_area_delegates_to_issue_82(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def delegated(
        arguments: list[str],
        **_: object,
    ) -> int:
        captured.append(arguments)
        return 17

    monkeypatch.setattr("ai_multi_agent_platform.cli.issue_81.issue_82_run_cli", delegated)

    code = run_cli(["repository", "list"])

    assert code == 17
    assert captured == [["repository", "list"]]


def test_distribution_script_points_at_issue_81_composition() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'platform = "ai_multi_agent_platform.cli.issue_81:main"' in pyproject
