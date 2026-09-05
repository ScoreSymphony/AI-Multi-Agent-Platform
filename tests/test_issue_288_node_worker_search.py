from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import AuthorizationDecision
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    AuthorizationRequest,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.distributed import (
    AcceleratorResource,
    DistributedRegistry,
    DistributedRuntime,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
    register_distributed_control_plane,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.search import LocalSearchProvider, SearchDocument
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

NOW = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
NODE_PRIVATE_REF = "provider-private-node-288"
WORKER_PRIVATE_REF = "provider-private-worker-288"
ACCELERATOR_PRIVATE_ID = "provider-private-accelerator-288"


class SelectiveAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_actions: set[str] | None = None) -> None:
        super().__init__()
        self.denied_actions = denied_actions or set()

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action in self.denied_actions:
            return AuthorizationDecision(False, reason="hidden by issue #288 test policy")
        return AuthorizationDecision(True, reason="visible by issue #288 test policy")


class RecordingSearchProvider(LocalSearchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.last_documents: tuple[SearchDocument, ...] = ()

    async def rebuild(
        self,
        documents: tuple[SearchDocument, ...],
        context: OperationContext,
    ) -> None:
        self.last_documents = documents
        await super().rebuild(documents, context)


class UnavailableSearchProvider(LocalSearchProvider):
    async def rebuild(
        self,
        documents: tuple[SearchDocument, ...],
        context: OperationContext,
    ) -> None:
        del documents, context
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "search provider unavailable",
            retryable=True,
        )


def _node_and_worker() -> tuple[NodeRecord, WorkerRecord]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="Issue 288 GPU node",
        resources=ResourceSnapshot(
            cpu_cores_total=16.0,
            cpu_cores_available=12.0,
            ram_total_bytes=64_000,
            ram_available_bytes=48_000,
            storage_total_bytes=1_000_000,
            storage_available_bytes=800_000,
            accelerators=(
                AcceleratorResource(
                    accelerator_id=ACCELERATOR_PRIVATE_ID,
                    kind="gpu",
                    vendor="NVIDIA",
                    model="RTX 4090",
                    memory_total_bytes=24_000,
                    memory_available_bytes=20_000,
                ),
            ),
        ),
        labels=("gpu", "linux", "trusted"),
        os_name="linux",
        platform="linux-x86_64",
        architecture="x86_64",
        supported_runtimes=("python", "container"),
        model_refs=("model-local-288",),
        capability_refs=("capability-code-288", "capability-vision-288"),
        trust_level="trusted",
        adapter_metadata=(
            AdapterMetadata(
                namespace="provider-private",
                values={"infrastructure_node_id": NODE_PRIVATE_REF},
            ),
        ),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        worker_type="execution",
        supported_executors=("reference", "sandbox"),
        capability_refs=("capability-code-288",),
        supported_runtimes=("python",),
        model_refs=("model-local-288",),
        concurrency_limit=4,
        worker_version="288.1",
        adapter_metadata=(
            AdapterMetadata(
                namespace="provider-private",
                values={"infrastructure_worker_id": WORKER_PRIVATE_REF},
            ),
        ),
    )
    return node, worker


def _stack(
    authorization: FakeAuthorizationProvider | None = None,
    *,
    search_provider: LocalSearchProvider | None = None,
) -> tuple[
    ControlPlane,
    ControlPlaneHTTP,
    DistributedRuntime,
    NodeRecord,
    WorkerRecord,
]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization or FakeAuthorizationProvider(),
        search_provider=search_provider,
    )
    runtime = DistributedRuntime(DistributedRegistry())
    node, worker = _node_and_worker()
    runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
    register_distributed_control_plane(control_plane, runtime)
    return control_plane, ControlPlaneHTTP(control_plane), runtime, node, worker


async def _search(
    http: ControlPlaneHTTP,
    query: dict[str, str],
) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", query=query)
    )
    assert response.status == 200
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    return [item for item in items if isinstance(item, dict)]


def test_exact_node_and_worker_lookup_preserves_canonical_identity_and_refs() -> None:
    async def scenario() -> None:
        _, http, _, node, worker = _stack()

        for resource_type, resource_id, collection in (
            ("node", node.node_id, "nodes"),
            ("worker", worker.worker_id, "workers"),
        ):
            page = await _search(
                http,
                {"id": resource_id, "type": resource_type},
            )
            assert page["total"] == 1
            item = _items(page)[0]
            assert item["resource_type"] == resource_type
            assert item["resource_id"] == resource_id
            assert item["canonical_ref"] == f"/api/v1/{collection}/{resource_id}"
            assert item["access"] == "authorized"

    asyncio.run(scenario())


def test_status_health_capability_runtime_and_resource_class_discovery() -> None:
    async def scenario() -> None:
        _, http, _, node, worker = _stack()

        online = await _search(http, {"type": "node", "status": "online"})
        assert {item["resource_id"] for item in _items(online)} == {node.node_id}

        healthy = await _search(http, {"type": "worker", "status": "healthy"})
        assert {item["resource_id"] for item in _items(healthy)} == {worker.worker_id}

        tagged = await _search(http, {"type": "node", "tag": "gpu,trusted"})
        assert {item["resource_id"] for item in _items(tagged)} == {node.node_id}

        for query, expected in (
            ("capability-code-288", {node.node_id, worker.worker_id}),
            ("container", {node.node_id}),
            ("reference", {worker.worker_id}),
            ("RTX 4090", {node.node_id}),
        ):
            page = await _search(http, {"q": query, "type": "node,worker"})
            assert {item["resource_id"] for item in _items(page)} == expected

    asyncio.run(scenario())


def test_node_worker_search_authorization_hides_items_counts_snippets_and_exact_existence() -> None:
    async def scenario() -> None:
        authorization = SelectiveAuthorization({"node:list", "worker:list"})
        _, http, _, node, worker = _stack(authorization)

        broad = await _search(http, {"q": "288", "type": "node,worker"})
        assert broad["total"] == 0
        assert _items(broad) == []
        serialized = repr(broad)
        assert node.node_id not in serialized
        assert worker.worker_id not in serialized
        assert "Issue 288 GPU node" not in serialized

        for resource_type, resource_id in (
            ("node", node.node_id),
            ("worker", worker.worker_id),
        ):
            exact = await _search(
                http,
                {"id": resource_id, "type": resource_type},
            )
            assert exact["total"] == 0
            assert _items(exact) == []
            assert resource_id not in repr(exact)

        actions = {call.action for call in authorization.calls}
        assert "node:list" in actions
        assert "worker:list" in actions

    asyncio.run(scenario())


def test_search_projection_excludes_provider_private_metadata_and_accelerator_ids() -> None:
    async def scenario() -> None:
        provider = RecordingSearchProvider()
        control_plane, http, _, node, worker = _stack(search_provider=provider)

        await control_plane.rebuild_search_index(correlation_id="issue-288-private-metadata")
        node_worker_documents = tuple(
            document
            for document in provider.last_documents
            if document.resource_type in {"node", "worker"}
        )
        assert {(document.resource_type, document.resource_id) for document in node_worker_documents} == {
            ("node", node.node_id),
            ("worker", worker.worker_id),
        }
        serialized_documents = repr(node_worker_documents)
        assert NODE_PRIVATE_REF not in serialized_documents
        assert WORKER_PRIVATE_REF not in serialized_documents
        assert ACCELERATOR_PRIVATE_ID not in serialized_documents

        for private_value in (
            NODE_PRIVATE_REF,
            WORKER_PRIVATE_REF,
            ACCELERATOR_PRIVATE_ID,
        ):
            page = await _search(http, {"q": private_value, "type": "node,worker"})
            assert page["total"] == 0
            assert _items(page) == []

    asyncio.run(scenario())


def test_rebuild_tracks_node_worker_updates_and_deregistration_without_search_authority() -> None:
    async def scenario() -> None:
        _, http, runtime, node, worker = _stack()

        runtime.set_node_maintenance(node.node_id, maintenance=True)
        maintenance = await _search(http, {"type": "node", "status": "maintenance"})
        assert {item["resource_id"] for item in _items(maintenance)} == {node.node_id}

        runtime.registry.deregister_worker(worker.worker_id)
        removed_worker = await _search(
            http,
            {"id": worker.worker_id, "type": "worker"},
        )
        assert removed_worker["total"] == 0
        assert _items(removed_worker) == []

        runtime.registry.deregister_node(node.node_id)
        removed_node = await _search(
            http,
            {"id": node.node_id, "type": "node"},
        )
        assert removed_node["total"] == 0
        assert _items(removed_node) == []

    asyncio.run(scenario())


def test_distributed_registration_preserves_search_provider_degraded_behavior() -> None:
    async def scenario() -> None:
        _, http, _, _, _ = _stack(search_provider=UnavailableSearchProvider())

        response = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"q": "node", "type": "node,worker"},
            )
        )
        assert response.status == 503
        assert isinstance(response.body, dict)
        assert response.body["code"] == "unavailable"
        assert response.body["retryable"] is True

    asyncio.run(scenario())
