from __future__ import annotations

import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_multi_agent_platform.cli.app import run_cli
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileStore


class MarketplaceTransport:
    def __init__(self, *, error_path: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, list[str]], dict[str, str], object]] = []
        self.error_path = error_path

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
        decoded: object = None
        if body is not None:
            decoded = json.loads(body.decode("utf-8"))
        self.calls.append(
            (
                method,
                parsed.path,
                parse_qs(parsed.query),
                dict(headers),
                decoded,
            )
        )
        if self.error_path == parsed.path:
            return RawResponse(
                status=409,
                body=json.dumps(
                    {
                        "code": "conflict",
                        "category": "conflict",
                        "message": "blocked",
                        "retryable": False,
                        "request_id": "request_error",
                        "correlation_id": "corr_error",
                        "details": {"marketplace_reason": "dependency_block"},
                    }
                ).encode("utf-8"),
                headers={"x-api-version": "v1"},
            )
        return RawResponse(
            status=200,
            body=json.dumps(
                {
                    "id": "example.asset@1.2.3",
                    "kind": "tool",
                    "status": "ok",
                }
            ).encode("utf-8"),
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


def test_marketplace_search_uses_unified_collection_and_first_class_filters(
    tmp_path: Path,
) -> None:
    transport = MarketplaceTransport()
    stdout = StringIO()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "--json",
            "marketplace",
            "search",
            "agent",
            "--limit",
            "10",
            "--sort",
            "name",
            "--kind",
            "tool",
            "--kind",
            "skill",
            "--tag",
            "developer",
            "--category",
            "tools",
            "--publisher",
            "example",
            "--source",
            "source-a",
            "--license",
            "MIT",
            "--trust",
            "reviewed",
            "--maturity",
            "stable",
            "--installed",
            "true",
            "--update-available",
            "false",
            "--compatible",
            "true",
            "--platform-version",
            "1.0.0",
        ],
        transport=transport,
        stdout=stdout,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["data"]["kind"] == "tool"
    method, path, query, headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/registry-items"
    assert body is None
    assert headers["x-principal-ref"] == "user:test"
    assert query["q"] == ["agent"]
    assert query["limit"] == ["10"]
    assert query["sort"] == ["name"]
    assert query["filter[kind]"] == ["tool,skill"]
    assert query["filter[tag]"] == ["developer"]
    assert query["filter[category]"] == ["tools"]
    assert query["filter[publisher]"] == ["example"]
    assert query["filter[source]"] == ["source-a"]
    assert query["filter[license]"] == ["MIT"]
    assert query["filter[trust]"] == ["reviewed"]
    assert query["filter[maturity]"] == ["stable"]
    assert query["filter[installed]"] == ["true"]
    assert query["filter[update_available]"] == ["false"]
    assert query["filter[compatible]"] == ["true"]
    assert query["filter[platform_version]"] == ["1.0.0"]


def test_marketplace_kinds_lists_registered_component_metadata(tmp_path: Path) -> None:
    transport = MarketplaceTransport()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "marketplace",
            "kinds",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    method, path, query, _headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/marketplace-kinds"
    assert body is None
    assert query == {
        "limit": ["200"],
        "sort": ["kind"],
        "direction": ["asc"],
    }


def test_marketplace_updates_reuses_canonical_update_filter(tmp_path: Path) -> None:
    transport = MarketplaceTransport()

    code = run_cli(
        [
            "--config",
            str(_config(tmp_path)),
            "marketplace",
            "updates",
            "--kind",
            "application",
            "--source",
            "source-a",
        ],
        transport=transport,
        stdout=StringIO(),
    )

    assert code == 0
    method, path, query, _headers, body = transport.calls[0]
    assert method == "GET"
    assert path == "/api/v1/registry-items"
    assert body is None
    assert query["filter[update_available]"] == ["true"]
    assert query["filter[kind]"] == ["application"]
    assert query["filter[source]"] == ["source-a"]


def test_marketplace_show_and_status_share_unified_detail_surface(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = MarketplaceTransport()

    for command in ("show", "status"):
        code = run_cli(
            [
                "--config",
                str(config),
                "marketplace",
                command,
                "example.asset",
                "1.2.3",
                "--source",
                "source-a",
            ],
            transport=transport,
            stdout=StringIO(),
        )
        assert code == 0

    assert [call[:2] for call in transport.calls] == [
        ("GET", "/api/v1/registry-items/source-a%3A%3Aexample.asset%401.2.3"),
        ("GET", "/api/v1/registry-items/source-a%3A%3Aexample.asset%401.2.3"),
    ]


def test_marketplace_preview_and_mutations_dispatch_canonical_commands(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    transport = MarketplaceTransport()

    preview_code = run_cli(
        [
            "--config",
            str(config),
            "marketplace",
            "preview",
            "example.asset",
            "1.2.3",
            "--source",
            "source-a",
            "--idempotency-key",
            "preview-key",
        ],
        transport=transport,
        stdout=StringIO(),
    )
    assert preview_code == 0

    for action in ("install", "update"):
        code = run_cli(
            [
                "--config",
                str(config),
                "--yes",
                "marketplace",
                action,
                "example.asset",
                "1.2.3",
                "--source",
                "source-a",
                "--idempotency-key",
                f"{action}-key",
            ],
            transport=transport,
            stdout=StringIO(),
        )
        assert code == 0

    uninstall_code = run_cli(
        [
            "--config",
            str(config),
            "--yes",
            "marketplace",
            "uninstall",
            "example.asset",
            "--idempotency-key",
            "uninstall-key",
        ],
        transport=transport,
        stdout=StringIO(),
    )
    assert uninstall_code == 0

    assert [call[1] for call in transport.calls] == [
        "/api/v1/commands/marketplace.preview",
        "/api/v1/commands/marketplace.install",
        "/api/v1/commands/marketplace.update",
        "/api/v1/commands/marketplace.uninstall",
    ]
    assert transport.calls[0][3]["idempotency-key"] == "preview-key"
    assert transport.calls[0][4] == {
        "resource_ref": "example.asset",
        "version": "1.2.3",
        "source_registry": "source-a",
    }
    assert transport.calls[1][3]["idempotency-key"] == "install-key"
    assert transport.calls[1][4] == {
        "resource_ref": "example.asset",
        "version": "1.2.3",
        "source_registry": "source-a",
    }
    assert transport.calls[2][3]["idempotency-key"] == "update-key"
    assert transport.calls[2][4] == {
        "resource_ref": "example.asset",
        "version": "1.2.3",
        "source_registry": "source-a",
    }
    assert transport.calls[3][3]["idempotency-key"] == "uninstall-key"
    assert transport.calls[3][4] == {"resource_ref": "example.asset"}


def test_marketplace_mutations_require_global_confirmation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    transport = MarketplaceTransport()
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "marketplace",
            "install",
            "example.asset",
            "1.2.3",
        ],
        transport=transport,
        stderr=stderr,
    )

    assert code == 2
    assert transport.calls == []
    assert "--yes" in stderr.getvalue()


def test_marketplace_json_error_preserves_canonical_machine_contract(tmp_path: Path) -> None:
    config = _config(tmp_path)
    error_path = "/api/v1/commands/marketplace.install"
    transport = MarketplaceTransport(error_path=error_path)
    stderr = StringIO()

    code = run_cli(
        [
            "--config",
            str(config),
            "--json",
            "--yes",
            "marketplace",
            "install",
            "example.asset",
            "1.2.3",
        ],
        transport=transport,
        stderr=stderr,
    )

    assert code == 3
    payload = json.loads(stderr.getvalue())
    assert payload["code"] == "conflict"
    assert payload["category"] == "conflict"
    assert payload["details"]["marketplace_reason"] == "dependency_block"

def test_marketplace_future_kind_matches_core_control_plane_and_cli(
    tmp_path: Path,
) -> None:
    import asyncio

    from cli_test_helpers import ControlPlaneRecordingTransport, invoke_cli_json, page_items

    from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
    from ai_multi_agent_platform.control_plane.models import RequestContext
    from ai_multi_agent_platform.distribution import (
        DistributionRoute,
        DistributionService,
        JsonRegistryInstallationStore,
        LocalRegistryProvider,
        MarketplaceKindDescriptor,
        MarketplaceKindHandlerRegistry,
        MarketplaceKindRegistry,
        RegistryItem,
        RegistryManifestReference,
        RegistrySource,
        TrustStatus,
        ValidationContext,
        register_distribution_control_plane,
    )
    from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
    from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

    class ValidationResolver:
        async def resolve(self, context: RequestContext) -> ValidationContext:
            del context
            return ValidationContext("1.0.0")

    class FutureOwner:
        kind = "notebook_extension"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
            del item
            return {"runtime": "notebook-host"}

        async def install(self, item: RegistryItem, artifact: bytes) -> object:
            del artifact
            self.calls.append(("install", item.item_id))
            return item.item_id

        async def update(self, item: RegistryItem, artifact: bytes) -> object:
            del artifact
            self.calls.append(("update", item.item_id))
            return item.item_id

        async def uninstall(self, item: RegistryItem) -> object:
            self.calls.append(("uninstall", item.item_id))
            return item.item_id

        async def status(self, item: RegistryItem) -> object:
            return {"item_id": item.item_id, "owner_state": "installed"}

        def describe(self, item: RegistryItem) -> dict[str, object]:
            return {"item_id": item.item_id, "owner": "notebook"}

    item = RegistryItem(
        item_id="acceptance.notebook",
        item_type="notebook_extension",
        name="Notebook Extension",
        description="Cross-layer future-kind fixture",
        version="1.0.0",
        publisher="acceptance",
        source=RegistrySource(
            "https://example.invalid/notebook",
            "acceptance.notebook@1.0.0",
            revision="v1",
        ),
        license="MIT",
        provenance="acceptance-release",
        trust_status=TrustStatus.REVIEWED,
        manifest=RegistryManifestReference(
            kind="notebook_extension",
            reference="manifests/notebook.json",
            schema_version="1",
        ),
    )
    artifact = b"notebook-extension"
    owner = FutureOwner()
    distribution = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): artifact},
            provider_id="acceptance",
        ),
        installations=JsonRegistryInstallationStore(tmp_path / "future-kind-installations.json"),
        kind_handlers=MarketplaceKindHandlerRegistry((owner,)),
        kind_registry=MarketplaceKindRegistry(
            (
                MarketplaceKindDescriptor(
                    "notebook_extension",
                    "Notebook Extension",
                    DistributionRoute.KIND_HANDLER,
                    supports_install=True,
                    supports_update=True,
                    supports_uninstall=True,
                ),
            )
        ),
    )
    validation = ValidationContext("1.0.0")
    core_item = distribution.get(
        item.item_id,
        item.version,
        source_registry="acceptance",
    )
    core_preview = distribution.preview(
        item.item_id,
        item.version,
        validation,
        source_registry="acceptance",
    )
    assert core_item.kind == "notebook_extension"
    assert core_item.source_registry == "acceptance"
    assert core_preview.item == core_item
    assert core_preview.decision.operation.value == "install"

    events = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        ),
        events=events,
    )
    register_distribution_control_plane(
        control_plane,
        distribution,
        validation_context_resolver=ValidationResolver(),
    )
    http = ControlPlaneHTTP(control_plane)
    headers = {
        "x-request-id": "request_marketplace_cross_layer",
        "x-correlation-id": "corr_marketplace_cross_layer",
        "x-principal-ref": "user:test",
    }

    api_list = asyncio.run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/registry-items",
                headers=headers,
                query={
                    "filter[kind]": "notebook_extension",
                    "filter[source]": "acceptance",
                    "sort": "id",
                    "direction": "asc",
                    "limit": "50",
                },
                body={},
            )
        )
    )
    assert api_list.status == 200
    api_items = api_list.body["items"]  # type: ignore[index]
    assert isinstance(api_items, list)
    assert len(api_items) == 1
    api_item = api_items[0]
    assert isinstance(api_item, dict)

    transport = ControlPlaneRecordingTransport(http)
    config = _config(tmp_path)
    code, listed, error = invoke_cli_json(
        config,
        transport,
        "marketplace",
        "list",
        "--kind",
        "notebook_extension",
        "--source",
        "acceptance",
    )
    assert code == 0
    assert error == ""
    cli_item = page_items(listed)[0]
    assert cli_item == api_item
    assert cli_item["item_id"] == core_item.item_id
    assert cli_item["kind"] == core_item.kind
    assert cli_item["version"] == core_item.version
    assert cli_item["source_registry"] == core_item.source_registry
    assert cli_item["id"] == "acceptance.notebook@1.0.0"
    assert cli_item["qualified_id"] == "acceptance::acceptance.notebook@1.0.0"
    assert "provider_id" not in cli_item

    preview_body = {
        "resource_ref": item.item_id,
        "version": item.version,
        "source_registry": "acceptance",
    }
    api_preview = asyncio.run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/marketplace.preview",
                headers={**headers, "idempotency-key": "api-preview"},
                query={},
                body=preview_body,
            )
        )
    )
    assert api_preview.status == 200
    assert isinstance(api_preview.body, dict)

    code, previewed, error = invoke_cli_json(
        config,
        transport,
        "marketplace",
        "preview",
        item.item_id,
        item.version,
        "--source",
        "acceptance",
        "--idempotency-key",
        "cli-preview",
    )
    assert code == 0
    assert error == ""
    cli_preview = previewed["data"]
    assert cli_preview == api_preview.body
    assert cli_preview["decision"]["operation"] == core_preview.decision.operation.value
    assert cli_preview["item"]["owner_extension"]["requirements"] == {
        "runtime": "notebook-host"
    }
    assert cli_preview["item"]["owner_extension"]["details"] is None
    assert cli_preview["item"]["integrity"]["sha256"] == core_item.integrity.sha256
    assert cli_preview["item"]["trust"] == core_item.trust_status.value

    code, installed, error = invoke_cli_json(
        config,
        transport,
        "--yes",
        "marketplace",
        "install",
        item.item_id,
        item.version,
        "--source",
        "acceptance",
        "--idempotency-key",
        "cli-install",
    )
    assert code == 0
    assert error == ""
    assert installed["data"]["status"] == "applied"
    assert owner.calls == [("install", item.item_id)]

    code, status, error = invoke_cli_json(
        config,
        transport,
        "marketplace",
        "status",
        item.item_id,
        item.version,
        "--source",
        "acceptance",
    )
    assert code == 0
    assert error == ""
    status_item = status["data"]
    assert status_item["installed"] is True
    assert status_item["owner_extension"]["status"]["owner_state"] == "installed"
    assert status_item["owner_extension"]["details"]["owner"] == "notebook"

