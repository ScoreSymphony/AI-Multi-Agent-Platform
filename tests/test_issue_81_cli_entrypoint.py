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
    assert len(transport.calls) == 1
    method, path, headers, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/v1/commands/registry.preview"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/json"
    assert headers["x-principal-ref"] == "user:test"
    assert headers["x-request-id"].startswith("request_")
    assert headers["x-correlation-id"].startswith("corr_")
    assert headers["idempotency-key"].startswith("cli_")
    assert body == {"resource_ref": "example.asset", "version": "1.2.3"}


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
    assert path == "/api/v1/commands/registry.activate"
    assert body == {"resource_ref": "example.asset", "version": "1.2.3"}


def test_learning_candidate_list_is_reachable_through_platform_entrypoint(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()

    code = run_cli(
        ["--config", str(config), "--json", "learning", "candidate", "list"],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    assert len(transport.calls) == 1
    method, path, headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/learning-candidates"
    assert headers["accept"] == "application/json"
    assert headers["x-principal-ref"] == "user:test"
    assert body is None


def test_learning_promotion_requires_global_yes_before_transport(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "learning",
            "promote",
            "learning_candidate_example",
            "--expected-revision",
            "4",
        ],
        transport=transport,
        stderr=stderr,
    )

    assert code == 2
    assert transport.calls == []
    assert "--yes" in stderr.getvalue()


def test_learning_promotion_with_yes_dispatches_exact_candidate_revision(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()

    code = run_cli(
        [
            "--config",
            str(config),
            "--yes",
            "learning",
            "promote",
            "learning_candidate_example",
            "--expected-revision",
            "4",
            "--approval-id",
            "approval_example",
            "--idempotency-key",
            "learning-promote-test",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    assert len(transport.calls) == 1
    method, path, headers, body = transport.calls[0]
    assert method == "POST"
    assert path == "/api/v1/commands/learning.promote"
    assert headers["idempotency-key"] == "learning-promote-test"
    assert body == {
        "resource_ref": "learning_candidate_example",
        "expected_revision": 4,
        "approval_id": "approval_example",
    }


def test_generic_extension_execute_cannot_bypass_learning_domain_safeguards(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    transport = RecordingTransport()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "--yes",
            "extension",
            "execute",
            "learning.promote",
            "learning_candidate_example",
            "--payload",
            '{"expected_revision":4}',
            "--idempotency-key",
            "learning-promote-bypass-test",
        ],
        transport=transport,
        stderr=stderr,
    )

    assert code == 2
    assert transport.calls == []
    assert "first-class `platform learning` domain" in stderr.getvalue()


def test_non_owned_area_delegates_to_issue_82(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_non_learning_extension_execution_still_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    def delegated(
        arguments: list[str],
        **_: object,
    ) -> int:
        captured.append(arguments)
        return 19

    monkeypatch.setattr("ai_multi_agent_platform.cli.issue_81.issue_82_run_cli", delegated)
    arguments = [
        "extension",
        "execute",
        "custom.mutate",
        "resource:1",
        "--payload",
        "{}",
        "--idempotency-key",
        "custom-test",
    ]

    code = run_cli(arguments)

    assert code == 19
    assert captured == [arguments]


def test_distribution_script_points_at_issue_81_composition() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'platform = "ai_multi_agent_platform.cli.issue_81:main"' in pyproject
