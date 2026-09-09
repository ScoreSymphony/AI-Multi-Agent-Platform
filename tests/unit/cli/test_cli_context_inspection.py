from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.context import (
    ContextBudget,
    ContextCandidate,
    ContextEntryRole,
    ContextResolver,
    ContextRunBinding,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
    InMemoryContextBundleRepository,
    InMemoryContextRunBindingRepository,
    register_context_control_plane,
)
from ai_multi_agent_platform.context.resolver import ContextAssemblyRequest
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class _RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str, dict[str, str]]] = []

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
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query))
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append((method, parsed.path, query))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=query,
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _bundle():
    task_id = new_id("task")
    candidate = ContextCandidate(
        source=ContextSourceRef(
            ContextSourceType.TASK,
            task_id,
            revision="1",
            digest=hashlib.sha256(f"{task_id}:1".encode()).hexdigest(),
        ),
        role=ContextEntryRole.CONTEXT,
        selection_reason="CLI context inspection regression",
        mandatory=True,
        inline_content="never expose this inline context value through CLI inspection",
        trust=ContextTrust.TRUSTED,
        relevance=1.0,
    )
    request = ContextAssemblyRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        actor=ActorIdentity("user:issue-650", ActorType.HUMAN),
        operation=OperationContext(correlation_id=task_id),
        candidates=(candidate,),
        budget=ContextBudget(max_tokens=4096, max_bytes=16384, max_items=16),
    )
    return asyncio.run(ContextResolver(FakeAuthorizationProvider()).resolve(request))


def _http(bundle, binding: ContextRunBinding) -> ControlPlaneHTTP:
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    control_plane = ControlPlane(kernel=kernel, events=events)
    bundles = InMemoryContextBundleRepository()
    bundles.put(bundle)
    bindings = InMemoryContextRunBindingRepository()
    bindings.put(binding)
    register_context_control_plane(control_plane, bundles, bindings)
    return ControlPlaneHTTP(control_plane)


def _invoke(
    config: Path,
    transport: _RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
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


def test_generic_extension_cli_traces_run_binding_to_exact_context_bundle_without_value_leak(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    agent_run_id = new_id("agent_run")
    binding = ContextRunBinding(
        agent_run_id=agent_run_id,
        run_id=bundle.run_id,
        task_id=bundle.task_id,
        agent_id=bundle.agent_id,
        agent_revision=bundle.agent_revision,
        context_bundle_id=bundle.context_bundle_id,
        context_bundle_digest=bundle.digest,
        resolver_version=bundle.resolver_version,
        policy_version=bundle.policy_version,
        orchestrator_adapter_id="reference-context-orchestrator/v1",
        created_at=datetime.now(UTC),
    )
    transport = _RecordingTransport(_http(bundle, binding))
    config = tmp_path / "cli.json"

    code, binding_page, error = _invoke(
        config,
        transport,
        "extension",
        "list",
        "context-run-bindings",
        "--filter",
        f"run_id={bundle.run_id}",
    )
    assert code == 0 and not error
    page_data = binding_page["data"]
    assert isinstance(page_data, dict)
    items = page_data["items"]
    assert isinstance(items, list) and len(items) == 1
    shown_binding = cast(dict[str, Any], items[0])
    assert shown_binding["agent_run_id"] == agent_run_id
    assert shown_binding["context_bundle_id"] == bundle.context_bundle_id
    assert shown_binding["context_bundle_digest"] == bundle.digest

    code, bundle_response, error = _invoke(
        config,
        transport,
        "extension",
        "show",
        "context-bundles",
        bundle.context_bundle_id,
    )
    assert code == 0 and not error
    shown_bundle = bundle_response["data"]
    assert isinstance(shown_bundle, dict)
    assert shown_bundle["run_id"] == bundle.run_id
    assert shown_bundle["digest"] == bundle.digest
    serialized = json.dumps(shown_bundle, sort_keys=True)
    assert "never expose this inline context value" not in serialized

    assert any(
        path == "/api/v1/context-run-bindings" and query.get("filter[run_id]") == bundle.run_id
        for _, path, query in transport.calls
    )
    assert any(
        path == f"/api/v1/context-bundles/{bundle.context_bundle_id}"
        for _, path, _ in transport.calls
    )
