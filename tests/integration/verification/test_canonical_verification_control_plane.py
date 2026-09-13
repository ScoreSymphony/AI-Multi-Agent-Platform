from __future__ import annotations

import asyncio

from ai_multi_agent_platform.coding_batches import (
    CodingBatchCoordinator,
    VerificationEvidence,
    WorkstreamResult,
)
from ai_multi_agent_platform.coding_batches.sqlite_catalog import SqliteCodingBatchCatalog
from ai_multi_agent_platform.coding_batches.sqlite_store import SqliteCodingBatchStore
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.coding_batch_contract import (
    CODING_BATCH_COLLECTION,
    coding_batch_command_handlers,
    coding_batch_resource_services,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

BASE = "a" * 40
OUTPUT = "b" * 40


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-872-control-plane",
        "X-Correlation-Id": "correlation-872-control-plane",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "test",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def test_control_plane_exposes_durable_coding_batch_state_and_safe_commands(tmp_path) -> None:
    async def scenario() -> None:
        store = SqliteCodingBatchStore(tmp_path / "coding-batches.sqlite3")
        coordinator = CodingBatchCoordinator(store=store)
        catalog = SqliteCodingBatchCatalog(store)
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            resource_services=coding_batch_resource_services(catalog),
            command_handlers=coding_batch_command_handlers(coordinator),
        )
        http = ControlPlaneHTTP(control_plane)

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert CODING_BATCH_COLLECTION in manifest.body["resources"]

        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/coding-batch.create",
                headers=_headers("coding-batch-create-1"),
                body={
                    "resource_ref": "repo-platform",
                    "repository_id": "repo-platform",
                    "target_ref": "main",
                    "base_revision": BASE,
                    "aggregation_policy": "all_required",
                    "concurrency_limit": 2,
                    "work_items": [
                        {
                            "work_item_id": "a",
                            "task_id": "task-a",
                            "plan_id": "plan-a",
                            "step_id": "step-a",
                            "affected_paths": ["src/a.py"],
                        }
                    ],
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        batch_id = created.body["id"]
        assert isinstance(batch_id, str)
        assert created.body["aggregation_policy"] == "all_required"

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{CODING_BATCH_COLLECTION}",
                headers=_headers(),
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        assert listed.body["total"] == 1

        resource = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{CODING_BATCH_COLLECTION}/{batch_id}",
                headers=_headers(),
            )
        )
        assert resource.status == 200
        assert isinstance(resource.body, dict)
        assert resource.body["workstreams"][0]["workspace_id"] is None

        coordinator.materialize_workstream(
            batch_id,
            "a",
            workspace_id="workspace-a",
            snapshot_id="snapshot-a",
            agent_revision="developer@1",
            agent_run_id="agent-run-a",
        )
        coordinator.start_workstream(batch_id, "a")
        coordinator.record_result(
            batch_id,
            "a",
            WorkstreamResult(OUTPUT, ("src/a.py",), "digest-a"),
        )
        coordinator.record_verification(
            batch_id,
            "a",
            VerificationEvidence("verification-a", OUTPUT, True),
        )
        coordinator.accept_workstream(batch_id, "a")

        candidate_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/coding-batch.integration-candidate",
                headers=_headers("coding-batch-integrate-1"),
                body={
                    "resource_ref": batch_id,
                    "current_target_revision": BASE,
                },
            )
        )
        assert candidate_response.status == 200
        assert isinstance(candidate_response.body, dict)
        candidates = candidate_response.body["integration_candidates"]
        assert isinstance(candidates, list)
        assert candidates[0]["ordered_workstream_ids"] == ["a"]
        assert candidates[0]["state"] == "ready"

        restarted_store = SqliteCodingBatchStore(tmp_path / "coding-batches.sqlite3")
        restarted_catalog = SqliteCodingBatchCatalog(restarted_store)
        restarted = restarted_catalog.get(batch_id)
        assert restarted is not None
        assert restarted.integration_candidates[0].ordered_revisions == (OUTPUT,)

    asyncio.run(scenario())
