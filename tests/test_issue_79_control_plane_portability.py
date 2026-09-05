from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.portability import (
    ExportSourceRegistry,
    IdPolicy,
    ImportContext,
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    PortableResource,
    PortabilityWorkflowService,
    ResourceExport,
    ResourceSerializerRegistry,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


@dataclass(frozen=True, slots=True)
class _DemoResource:
    resource_id: str
    name: str


class _DemoCodec:
    @property
    def resource_type(self) -> str:
        return "demo.resource"

    def serialize(self, value: object) -> ResourceExport:
        assert isinstance(value, _DemoResource)
        return ResourceExport(
            resource_id=value.resource_id,
            resource_version="1",
            payload={"name": value.name},
            id_policy=IdPolicy.REGENERATE,
        )

    def deserialize(
        self,
        resource: PortableResource,
        context: ImportContext,
    ) -> object:
        name = resource.payload.get("name")
        assert isinstance(name, str)
        return _DemoResource(
            resource_id=context.remap(resource.resource_type, resource.resource_id),
            name=name,
        )


class _DemoMutationHandler:
    def __init__(self, target: dict[str, _DemoResource]) -> None:
        self._target = target

    @property
    def resource_type(self) -> str:
        return "demo.resource"

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        assert isinstance(value, _DemoResource)
        if value.resource_id in self._target:
            raise ContractError(ErrorCode.CONFLICT, "target demo resource already exists")

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        assert isinstance(value, _DemoResource)
        self._target[value.resource_id] = value
        return value.resource_id

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        assert isinstance(token, str)
        self._target.pop(token, None)


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-portability-79",
        "X-Correlation-Id": "correlation-portability-79",
        "X-Principal-Ref": "user:portable",
        "X-Owner-Type": "user",
        "X-Owner-Id": "portable-owner",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _stack() -> tuple[ControlPlaneHTTP, dict[str, _DemoResource]]:
    source = {"demo_source": _DemoResource(resource_id="demo_source", name="Portable demo")}
    target: dict[str, _DemoResource] = {}

    serializers = ResourceSerializerRegistry()
    serializers.register(_DemoCodec())
    sources = ExportSourceRegistry()

    async def load_demo(resource_id: str) -> object:
        try:
            return source[resource_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"demo resource not found: {resource_id}") from exc

    sources.register("demo.resource", load_demo)
    mutations = ImportMutationRegistry()
    mutations.register(_DemoMutationHandler(target))
    preview = ImportPreviewService(
        resource_exists=lambda resource_type, resource_id: (
            resource_type == "demo.resource" and resource_id in target
        ),
        dependency_available=lambda requirement: False,
    )
    workflow = PortabilityWorkflowService(
        serializers=serializers,
        export_sources=sources,
        preview_service=preview,
        executor=ImportExecutor(serializers, mutations),
        platform_version="0.0.1",
        source_instance_id="test-instance",
    )

    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        portability_workflow=workflow,
    )
    return ControlPlaneHTTP(control_plane), target


def test_control_plane_binds_export_preview_and_import_without_client_owned_plan() -> None:
    async def scenario() -> None:
        http, target = _stack()

        manifest = await http.handle(
            HTTPRequest(method="GET", path="/api/v1", headers=_headers())
        )
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        commands = manifest.body["commands"]
        assert isinstance(resources, list)
        assert isinstance(commands, list)
        assert "portability-packages" in resources
        assert "portability-import-previews" in resources
        assert "portability-import-reports" in resources
        assert "portability.export" in commands
        assert "portability.package.validate" in commands
        assert "portability.preview" in commands
        assert "portability.import" in commands

        exported = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/portability.export",
                headers=_headers("portable-export-1"),
                body={
                    "resource_ref": "portability",
                    "resources": [
                        {"resource_type": "demo.resource", "resource_id": "demo_source"}
                    ],
                    "metadata": {"purpose": "control-plane-test"},
                },
            )
        )
        assert exported.status == 200
        assert isinstance(exported.body, dict)
        package_id = exported.body["package_id"]
        assert isinstance(package_id, str)
        assert exported.body["compatible"] is True
        package_document = exported.body["package"]
        assert isinstance(package_document, dict)

        validated = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/portability.package.validate",
                headers=_headers("portable-validate-1"),
                body={"resource_ref": "portability", "package": package_document},
            )
        )
        assert validated.status == 200
        assert isinstance(validated.body, dict)
        assert validated.body["package_id"] == package_id

        previewed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/portability.preview",
                headers=_headers("portable-preview-1"),
                body={"resource_ref": package_id},
            )
        )
        assert previewed.status == 200
        assert isinstance(previewed.body, dict)
        assert previewed.body["ready"] is True
        preview_id = previewed.body["preview_id"]
        assert isinstance(preview_id, str)
        mapping = previewed.body["id_mapping"]
        assert isinstance(mapping, list)
        assert len(mapping) == 1
        mapping_item = mapping[0]
        assert isinstance(mapping_item, dict)
        target_id = mapping_item["target_id"]
        assert isinstance(target_id, str)
        assert target_id != "demo_source"
        assert target == {}

        forged = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/portability.import",
                headers=_headers("portable-import-forged"),
                body={
                    "resource_ref": preview_id,
                    "id_mapping": [{"source_id": "demo_source", "target_id": "attacker"}],
                },
            )
        )
        assert forged.status == 400
        assert target == {}

        imported = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/portability.import",
                headers=_headers("portable-import-1"),
                body={"resource_ref": preview_id},
            )
        )
        assert imported.status == 200
        assert isinstance(imported.body, dict)
        assert imported.body["status"] == "succeeded"
        report_id = imported.body["report_id"]
        assert isinstance(report_id, str)
        assert target[target_id].name == "Portable demo"

        report = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/portability-import-reports/{report_id}",
                headers=_headers(),
            )
        )
        assert report.status == 200
        assert report.body == imported.body

    asyncio.run(scenario())


def test_portability_commands_require_normal_idempotency_boundary() -> None:
    async def scenario() -> None:
        http, target = _stack()
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/portability.export",
                headers=_headers(),
                body={
                    "resource_ref": "portability",
                    "resources": [
                        {"resource_type": "demo.resource", "resource_id": "demo_source"}
                    ],
                },
            )
        )
        assert response.status == 400
        assert target == {}

    asyncio.run(scenario())
