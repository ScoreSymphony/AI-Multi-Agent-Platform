from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from ai_multi_agent_platform.cli.client import ClientOptions, ControlPlaneClient, RawResponse
from ai_multi_agent_platform.cli.main import _build_parser
from ai_multi_agent_platform.cli.memory_knowledge import execute_knowledge, execute_memory
from ai_multi_agent_platform.cli.profiles import ProfileError


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

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
        self.calls.append((method, url, dict(headers), body))
        return RawResponse(
            status=200,
            headers={
                "content-type": "application/json",
                "x-request-id": "request-cli-251",
                "x-correlation-id": "correlation-cli-251",
                "x-api-version": "v1",
            },
            body=json.dumps({"ok": True}).encode(),
        )


def _client(transport: RecordingTransport) -> ControlPlaneClient:
    return ControlPlaneClient(
        ClientOptions(
            endpoint="http://127.0.0.1:8765",
            principal_ref="user:alice",
            owner_type="user",
            owner_id="alice",
        ),
        transport=transport,
    )


def _args(**values: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "command": "list",
        "limit": 50,
        "cursor": None,
        "sort": "id",
        "direction": "asc",
        "q": None,
        "fields": None,
        "scope": None,
        "scope_id": None,
        "project_id": None,
        "owner_ref": None,
        "include_expired": False,
        "include_superseded": False,
        "source_id": None,
        "mode": "keyword",
        "query": None,
        "idempotency_key": None,
        "yes": True,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def _confirm(args: argparse.Namespace, action: str, resource_ref: str) -> None:
    if not args.yes:
        raise ProfileError(f"confirmation required: {action} {resource_ref}")


def test_top_level_parser_registers_memory_and_knowledge_areas() -> None:
    parser = _build_parser()

    memory = parser.parse_args(
        [
            "memory",
            "list",
            "--scope",
            "user",
            "--scope-id",
            "alice",
        ]
    )
    assert memory.area == "memory"
    assert memory.command == "list"
    assert memory.scope == "user"
    assert memory.scope_id == "alice"

    knowledge = parser.parse_args(["knowledge", "search", "source-backed", "--mode", "keyword"])
    assert knowledge.area == "knowledge"
    assert knowledge.command == "search"
    assert knowledge.query == "source-backed"
    assert knowledge.mode == "keyword"


def test_memory_list_forwards_scope_filters_and_search() -> None:
    transport = RecordingTransport()
    execute_memory(
        _args(
            command="list",
            scope="workspace",
            scope_id="project_00000000-0000-4000-8000-000000000251",
            project_id="project_00000000-0000-4000-8000-000000000251",
            q="cadence",
            include_superseded=True,
        ),
        _client(transport),
        _confirm,
    )

    method, url, _, body = transport.calls[-1]
    assert method == "GET"
    assert body is None
    parsed = urlparse(url)
    assert parsed.path == "/api/v1/memory"
    query = parse_qs(parsed.query)
    assert query["q"] == ["cadence"]
    assert query["filter[scope]"] == ["workspace"]
    assert query["filter[scope_id]"] == ["project_00000000-0000-4000-8000-000000000251"]
    assert query["filter[include_superseded]"] == ["true"]


def test_memory_create_posts_explicit_origin_and_idempotency() -> None:
    transport = RecordingTransport()
    execute_memory(
        _args(
            command="create",
            scope_id="alice",
            scope="user",
            origin="imported",
            value_json='{"fact":"stable"}',
            retention="user_lifetime",
            expires_at=None,
            classification="private",
            metadata_json='{"source":"notes"}',
            provenance_json='[{"kind":"file","ref":"file_251"}]',
            idempotency_key="memory-create-251",
        ),
        _client(transport),
        _confirm,
    )

    method, url, headers, raw_body = transport.calls[-1]
    assert method == "POST"
    assert urlparse(url).path == "/api/v1/commands/memory.create"
    assert headers["idempotency-key"] == "memory-create-251"
    assert raw_body is not None
    payload = json.loads(raw_body)
    assert payload == {
        "resource_ref": "alice",
        "scope": "user",
        "scope_id": "alice",
        "origin": "imported",
        "value": {"fact": "stable"},
        "retention": "user_lifetime",
        "classification": "private",
        "metadata": {"source": "notes"},
        "provenance": [{"kind": "file", "ref": "file_251"}],
    }


def test_memory_delete_requires_confirmation_before_transport() -> None:
    transport = RecordingTransport()
    with pytest.raises(ProfileError):
        execute_memory(
            _args(
                command="delete",
                memory_id="memory_00000000-0000-4000-8000-000000000251",
                yes=False,
                idempotency_key="delete-251",
            ),
            _client(transport),
            _confirm,
        )
    assert transport.calls == []


def test_knowledge_search_uses_query_scoped_result_collection() -> None:
    transport = RecordingTransport()
    execute_knowledge(
        _args(
            command="search",
            query="source backed answer",
            source_id="knowledge_source_00000000-0000-4000-8000-000000000251",
            project_id="project_00000000-0000-4000-8000-000000000251",
            mode="hybrid",
        ),
        _client(transport),
        _confirm,
    )

    method, url, _, _ = transport.calls[-1]
    assert method == "GET"
    parsed = urlparse(url)
    assert parsed.path == "/api/v1/knowledge-results"
    query = parse_qs(parsed.query)
    assert query["q"] == ["source backed answer"]
    assert query["filter[mode]"] == ["hybrid"]
    assert query["filter[source_id]"] == ["knowledge_source_00000000-0000-4000-8000-000000000251"]


def test_knowledge_reindex_posts_revision_content_location_and_idempotency() -> None:
    transport = RecordingTransport()
    execute_knowledge(
        _args(
            command="reindex",
            source_id="knowledge_source_00000000-0000-4000-8000-000000000251",
            revision="r2",
            content="updated canonical source content",
            location="section:2",
            idempotency_key="reindex-251",
        ),
        _client(transport),
        _confirm,
    )

    method, url, headers, raw_body = transport.calls[-1]
    assert method == "POST"
    assert urlparse(url).path == "/api/v1/commands/knowledge.reindex"
    assert headers["idempotency-key"] == "reindex-251"
    assert raw_body is not None
    assert json.loads(raw_body) == {
        "resource_ref": "knowledge_source_00000000-0000-4000-8000-000000000251",
        "revision": "r2",
        "content": "updated canonical source content",
        "location": "section:2",
    }


def test_knowledge_detach_requires_confirmation_before_transport() -> None:
    transport = RecordingTransport()
    with pytest.raises(ProfileError):
        execute_knowledge(
            _args(
                command="detach",
                source_id="knowledge_source_00000000-0000-4000-8000-000000000251",
                yes=False,
            ),
            _client(transport),
            _confirm,
        )
    assert transport.calls == []
