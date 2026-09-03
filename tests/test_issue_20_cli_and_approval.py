from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    DiscoveredPlugin,
    ExtensionType,
    PluginCatalog,
    PluginRegistry,
    ReferenceCapabilityPlugin,
    StaticPluginSource,
    reference_manifest,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


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
        path = urlsplit(url).path
        self.calls.append((method, path, dict(headers), body))
        if method == "GET" and path.endswith("/plugin-candidates/plugin.reference-capability"):
            response_body: object = {
                "id": "plugin.reference-capability",
                "type": "plugin-candidate",
                "manifest_digest": "a" * 64,
            }
        elif method == "GET" and path.endswith("/plugins"):
            response_body = {"items": [], "next_cursor": None, "total": 0, "limit": 50}
        else:
            response_body = {"id": "plugin.reference-capability", "state": "installed"}
        return RawResponse(
            status=200,
            body=json.dumps(response_body).encode("utf-8"),
            headers={"x-api-version": "v1"},
        )


def _invoke(
    config: Path,
    transport: RecordingTransport,
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


def test_plugin_cli_uses_only_canonical_control_plane_paths(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = RecordingTransport()

    code, payload, error = _invoke(
        config,
        transport,
        "plugin",
        "candidate",
        "show",
        "plugin.reference-capability",
    )
    assert code == 0 and not error
    assert payload["data"]["manifest_digest"] == "a" * 64  # type: ignore[index]
    assert transport.calls[-1][0:2] == (
        "GET",
        "/api/v1/plugin-candidates/plugin.reference-capability",
    )

    code, payload, error = _invoke(config, transport, "plugin", "list")
    assert code == 0 and not error
    assert payload["data"]["total"] == 0  # type: ignore[index]
    assert transport.calls[-1][0:2] == ("GET", "/api/v1/plugins")


def test_plugin_cli_mutations_require_confirmation_and_preserve_exact_payload(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport = RecordingTransport()
    digest = "b" * 64

    code, payload, error = _invoke(
        config,
        transport,
        "plugin",
        "install",
        "plugin.reference-capability",
        "--manifest-digest",
        digest,
    )
    assert code == 2
    assert not payload
    assert "requires confirmation" in error
    assert transport.calls == []

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "plugin",
        "install",
        "plugin.reference-capability",
        "--manifest-digest",
        digest,
        "--idempotency-key",
        "plugin-install-cli",
    )
    assert code == 0 and not error
    method, path, headers, body = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/v1/commands/plugin.install"
    assert headers["idempotency-key"] == "plugin-install-cli"
    assert body is not None
    assert json.loads(body) == {
        "resource_ref": "plugin.reference-capability",
        "manifest_digest": digest,
    }

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "plugin",
        "configure",
        "plugin.reference-capability",
        "--configuration-json",
        '{"prefix":"cli:"}',
        "--idempotency-key",
        "plugin-configure-cli",
    )
    assert code == 0 and not error
    _, path, _, body = transport.calls[-1]
    assert path == "/api/v1/commands/plugin.configure"
    assert body is not None
    assert json.loads(body) == {
        "resource_ref": "plugin.reference-capability",
        "configuration": {"prefix": "cli:"},
    }


def test_plugin_cli_rejects_invalid_configuration_before_network(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport = RecordingTransport()

    code, payload, error = _invoke(
        config,
        transport,
        "--yes",
        "plugin",
        "configure",
        "plugin.reference-capability",
        "--configuration-json",
        "[]",
    )
    assert code == 2
    assert not payload
    assert "must be a JSON object" in error
    assert transport.calls == []


def _approval_stack() -> tuple[ControlPlaneHTTP, AuthorizationGate, PluginRegistry]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    capabilities = CapabilityRegistry()
    registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders={ExtensionType.CAPABILITY_PROVIDER: CapabilityRegistryBinder(capabilities)},
    )
    manifest = reference_manifest()
    catalog = PluginCatalog(
        StaticPluginSource(
            DiscoveredPlugin(
                manifest=manifest,
                runtime_factory=ReferenceCapabilityPlugin,
                install_source="bundled-reference",
            )
        )
    )
    policy = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:plugin-admin",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.READ,
                        AuthorizationAction.VIEW,
                    }
                ),
                approval_actions=frozenset(
                    {
                        AuthorizationAction.CREATE,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset({ResourceType.PLUGIN}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.PLUGIN}),
            ),
        )
    )
    gate = AuthorizationGate(policy)

    async def grants(context, candidate_manifest):
        del context
        return candidate_manifest.requested_permissions

    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=ControlPlaneAuthorizationBridge(gate),
        plugin_registry=registry,
        plugin_catalog=catalog,
        plugin_permission_resolver=grants,
    )
    return ControlPlaneHTTP(control_plane), gate, registry


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-plugin-approval",
        "X-Correlation-Id": "correlation-plugin-approval",
        "X-Principal-Ref": "user:plugin-admin",
        "X-Owner-Type": "user",
        "X-Owner-Id": "plugin-admin",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _approval_id(response) -> str:
    assert response.status == 403
    assert isinstance(response.body, dict)
    details = response.body["details"]
    assert isinstance(details, dict)
    assert details["authorization_outcome"] == "require_approval"
    approval_id = details["approval_id"]
    assert isinstance(approval_id, str)
    return approval_id


def _approve(gate: AuthorizationGate, approval_id: str) -> None:
    asyncio.run(
        gate.decide_approval(
            approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="correlation-plugin-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )


def test_plugin_approval_is_bound_to_exact_command_payload() -> None:
    async def request(http: ControlPlaneHTTP, *, key: str, body: dict[str, object]):
        return await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.configure",
                headers=_headers(key=key),
                body=body,
            )
        )

    http, gate, registry = _approval_stack()
    plugin_id = reference_manifest().plugin_id

    async def scenario() -> None:
        candidates = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/plugin-candidates",
                headers=_headers(),
            )
        )
        assert candidates.status == 200
        assert isinstance(candidates.body, dict)
        items = candidates.body["items"]
        assert isinstance(items, list) and len(items) == 1
        candidate = items[0]
        assert isinstance(candidate, dict)
        digest = candidate["manifest_digest"]
        assert isinstance(digest, str)

        install_body = {"resource_ref": plugin_id, "manifest_digest": digest}
        install_pending = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.install",
                headers=_headers(key="plugin-install-pending"),
                body=install_body,
            )
        )
        install_approval = _approval_id(install_pending)
        await gate.decide_approval(
            install_approval,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="correlation-plugin-install-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
        installed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.install",
                headers=_headers(key="plugin-install-approved"),
                body=install_body,
            )
        )
        assert installed.status == 200

        original_body = {
            "resource_ref": plugin_id,
            "configuration": {"prefix": "approved:"},
        }
        pending = await request(http, key="plugin-configure-pending", body=original_body)
        approval_id = _approval_id(pending)
        await gate.decide_approval(
            approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="correlation-plugin-configure-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )

        changed = await request(
            http,
            key="plugin-configure-changed",
            body={
                "resource_ref": plugin_id,
                "configuration": {"prefix": "changed:"},
            },
        )
        changed_approval = _approval_id(changed)
        assert changed_approval != approval_id
        assert registry.get(plugin_id).configured is False

        approved = await request(http, key="plugin-configure-approved", body=original_body)
        assert approved.status == 200
        assert registry.get(plugin_id).configured is True

    asyncio.run(scenario())
