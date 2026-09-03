from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    CapabilityToolProvider,
    CredentialRequirement,
    NativeEchoProvider,
    SideEffectClassification,
)
from ai_multi_agent_platform.capabilities.control_plane import capability_resource_services
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.contracts.types import (
    HealthStatus,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class InventoryProvider(CapabilityToolProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="inventory.test",
            provider_type="test",
            supported_operations=("discover", "invoke"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        permission = ("tools.secure",)
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id="tool.secure",
                    name="Secure Tool",
                    version="1.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    required_permissions=permission,
                    side_effects=SideEffectClassification.EXTERNAL,
                    health=HealthStatus.HEALTHY,
                    available=True,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="secure.v1",
            ),
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id="tool.secure",
                    name="Secure Tool",
                    version="2.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    required_permissions=permission,
                    side_effects=SideEffectClassification.EXTERNAL,
                    credential_requirement=CredentialRequirement.REQUIRED,
                    health=HealthStatus.UNAVAILABLE,
                    available=False,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="secure.v2",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, output={"ok": True})


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str]] = []

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
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append((method, parsed.path))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _http(*, registry: CapabilityRegistry | None = None) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=None if registry is None else capability_resource_services(registry),
    )
    return ControlPlaneHTTP(control_plane)


def _invoke(
    config: Path,
    transport: RecordingTransport,
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


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_registry_admin_inventory_is_not_permission_or_availability_filtered() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(InventoryProvider())

        assert registry.list_capabilities() == ()
        inventory = registry.inventory_capabilities()
        assert [(item.capability_id, item.version) for item in inventory] == [
            ("tool.secure", "1.0"),
            ("tool.secure", "2.0"),
        ]
        assert inventory[1].available is False
        assert inventory[1].credential_requirement is CredentialRequirement.REQUIRED
        providers = registry.inventory_providers()
        assert [provider.provider_id for provider in providers] == ["inventory.test"]

    asyncio.run(scenario())


def test_capability_cli_reads_canonical_registry_resources(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    asyncio.run(registry.register_provider(NativeEchoProvider()))
    asyncio.run(registry.register_provider(InventoryProvider()))
    transport = RecordingTransport(_http(registry=registry))
    config = tmp_path / "cli.json"

    code, capabilities, error = _invoke(config, transport, "capability", "list")
    assert code == 0 and not error
    capability_items = _items(capabilities)
    assert [item["id"] for item in capability_items] == ["tool.echo", "tool.secure"]
    secure = capability_items[1]
    assert secure["version_count"] == 2
    versions = secure["versions"]
    assert isinstance(versions, list)
    assert [version["version"] for version in versions] == ["1.0", "2.0"]
    assert versions[0]["required_permissions"] == ["tools.secure"]
    assert versions[0]["side_effects"] == "external"
    assert versions[1]["health"] == "unavailable"
    assert versions[1]["available"] is False
    assert versions[1]["credential_requirement"] == "required"

    code, capability, error = _invoke(
        config,
        transport,
        "capability",
        "show",
        "tool.secure",
    )
    assert code == 0 and not error
    assert capability["data"]["id"] == "tool.secure"
    assert capability["data"]["version_count"] == 2

    code, providers, error = _invoke(config, transport, "capability-provider", "list")
    assert code == 0 and not error
    provider_items = _items(providers)
    assert [item["id"] for item in provider_items] == ["inventory.test", "native.reference"]
    assert provider_items[0]["provider_type"] == "test"
    assert provider_items[0]["health"] == "healthy"
    assert provider_items[0]["available"] is True

    code, provider, error = _invoke(
        config,
        transport,
        "capability-provider",
        "show",
        "native.reference",
    )
    assert code == 0 and not error
    assert provider["data"]["id"] == "native.reference"
    assert provider["data"]["provider_type"] == "native"
    assert provider["data"]["supported_operations"] == ["invoke", "discover"]

    assert transport.calls == [
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/capabilities/tool.secure"),
        ("GET", "/api/v1/capability-providers"),
        ("GET", "/api/v1/capability-providers/native.reference"),
    ]


def test_capability_cli_has_no_backend_fallback_when_registry_is_absent(tmp_path: Path) -> None:
    transport = RecordingTransport(_http())
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(config, transport, "capability", "list")

    assert code == 3
    assert payload == {}
    assert '"code":"not_found"' in error
    assert '"message":"route not found"' in error
    assert transport.calls == [("GET", "/api/v1/capabilities")]
