from __future__ import annotations

import argparse

import pytest

from ai_multi_agent_platform.cli.client import ClientResponse
from ai_multi_agent_platform.cli.profiles import ProfileError
from ai_multi_agent_platform.cli.registry import add_registry_parser, execute_registry


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object, object]] = []

    def get(self, path: str, *, query: dict[str, str] | None = None) -> ClientResponse:
        self.calls.append(("GET", path, query, None))
        return ClientResponse(status=200, body={"ok": True})

    def post(
        self,
        path: str,
        *,
        body: object,
        idempotency_key: str | None = None,
    ) -> ClientResponse:
        self.calls.append(("POST", path, body, idempotency_key))
        return ClientResponse(status=200, body={"ok": True})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true")
    areas = parser.add_subparsers(dest="root_command", required=True)
    add_registry_parser(areas)
    return parser


def test_registry_list_and_show_use_canonical_resource_surface() -> None:
    client = RecordingClient()
    parser = _parser()

    list_args = parser.parse_args(["registry", "list", "--limit", "10", "--q", "agent"])
    execute_registry(list_args, client, lambda *_: None)  # type: ignore[arg-type]
    assert client.calls[-1] == (
        "GET",
        "/registry-items",
        {"limit": "10", "sort": "id", "direction": "asc", "q": "agent"},
        None,
    )

    show_args = parser.parse_args(["registry", "show", "example.asset", "1.2.3"])
    execute_registry(show_args, client, lambda *_: None)  # type: ignore[arg-type]
    assert client.calls[-1] == (
        "GET",
        "/registry-items/example.asset%401.2.3",
        None,
        None,
    )


def test_registry_preview_is_non_mutating_command_with_exact_version() -> None:
    client = RecordingClient()
    args = _parser().parse_args(
        ["registry", "preview", "example.asset", "1.2.3", "--idempotency-key", "preview-1"]
    )

    response = execute_registry(args, client, lambda *_: None)  # type: ignore[arg-type]

    assert response.status == 200
    assert client.calls == [
        (
            "POST",
            "/commands/registry.preview",
            {"resource_ref": "example.asset", "version": "1.2.3"},
            "preview-1",
        )
    ]


def test_registry_activate_requires_confirmation_before_command_dispatch() -> None:
    client = RecordingClient()
    args = _parser().parse_args(["registry", "activate", "example.asset", "1.2.3"])
    confirmations: list[tuple[str, str]] = []

    def confirm(_: argparse.Namespace, action: str, target: str) -> None:
        confirmations.append((action, target))

    execute_registry(args, client, confirm)  # type: ignore[arg-type]

    assert confirmations == [("activate registry item", "example.asset@1.2.3")]
    assert client.calls == [
        (
            "POST",
            "/commands/registry.activate",
            {"resource_ref": "example.asset", "version": "1.2.3"},
            None,
        )
    ]


def test_registry_filter_syntax_fails_closed() -> None:
    client = RecordingClient()
    args = _parser().parse_args(["registry", "list", "--filter", "broken"])

    with pytest.raises(ProfileError, match="FIELD=VALUE"):
        execute_registry(args, client, lambda *_: None)  # type: ignore[arg-type]

    assert client.calls == []
