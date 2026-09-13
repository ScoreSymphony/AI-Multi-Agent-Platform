from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HermesAdapterConfig,
    HermesCompatibilityStatus,
    HermesOrchestrator,
)
from ai_multi_agent_platform.contracts import OperationContext, OperationControl

HERMES_V0_21_2_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def _prepare_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    upstream_value = os.environ.get("HERMES_UPSTREAM_DIR")
    if not upstream_value:
        pytest.skip("set HERMES_UPSTREAM_DIR to run Hermes v0.21.2 restart tests")
    upstream = Path(upstream_value).resolve()
    assert os.environ.get("HERMES_UPSTREAM_REVISION") == HERMES_V0_21_2_REVISION
    assert (upstream / "gateway" / "platforms" / "api_server.py").is_file()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.syspath_prepend(str(upstream))
    return upstream


def _candidate_config(base_url: str) -> HermesAdapterConfig:
    return HermesAdapterConfig(
        enabled=True,
        base_url=base_url,
        pinned_revision=HERMES_V0_21_2_REVISION,
        compatibility_status=HermesCompatibilityStatus.UNVERIFIED_PIN,
        request_timeout_seconds=5.0,
        plan_timeout_seconds=15.0,
        poll_interval_seconds=0.01,
    )


def test_persisted_active_run_reconciles_after_runtime_recreation_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_upstream(tmp_path, monkeypatch)

    from aiohttp import web
    from aiohttp.test_utils import TestServer, make_mocked_request
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore

    async def scenario() -> None:
        store_path = tmp_path / "runs_idempotency.db"
        external_run_id = "run_issue959_active_restart"
        idempotency_key = "issue-959-active-restart"
        fingerprint = "issue-959-active-restart-fingerprint"
        status = {
            "object": "hermes.run",
            "run_id": external_run_id,
            "status": "running",
            "created_at": 1.0,
            "updated_at": 1.0,
        }

        before_restart = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        before_restart._run_idempotency_store.close()
        before_restart._run_idempotency_store = RunIdempotencyStore(str(store_path))
        scope_request = make_mocked_request("GET", f"/v1/runs/{external_run_id}")
        scope = before_restart._run_idempotency_scope(scope_request)
        outcome, stored = before_restart._run_idempotency_store.reserve(
            scope,
            idempotency_key,
            fingerprint,
            external_run_id,
            status,
            owner_pid=999_999_999,
            owner_started=1,
        )
        assert outcome == "created"
        assert stored["run_id"] == external_run_id
        before_restart._run_idempotency_store.close()

        after_restart = APIServerAdapter(
            PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "port": 0})
        )
        after_restart._run_idempotency_store.close()
        after_restart._run_idempotency_store = RunIdempotencyStore(str(store_path))

        app = web.Application()
        app["api_server_adapter"] = after_restart
        app.router.add_get("/v1/runs/{run_id}", after_restart._handle_get_run)
        server = TestServer(app)
        await server.start_server()
        try:
            orchestrator = HermesOrchestrator(
                _candidate_config(str(server.make_url("")).rstrip("/")),
                secret_resolver=lambda _: None,
            )
            reconciled = await orchestrator.reconcile_external_run(
                external_run_id,
                OperationContext(
                    correlation_id="issue-959-active-restart-reconcile",
                    control=OperationControl(timeout_seconds=5.0),
                ),
            )

            assert reconciled.external_run_id == external_run_id
            assert reconciled.status == "interrupted"

            persisted = after_restart._run_idempotency_store.status_for_run(
                scope,
                external_run_id,
            )
            assert persisted is not None
            assert persisted["status"]["status"] == "interrupted"
            assert "restarted before this run settled" in persisted["status"]["error"]

            replay_outcome, replay = after_restart._run_idempotency_store.lookup(
                scope,
                idempotency_key,
                fingerprint,
            )
            assert replay_outcome == "reused"
            assert replay is not None
            assert replay["run_id"] == external_run_id
            assert replay["status"]["status"] == "interrupted"
        finally:
            await server.close()
            after_restart._run_idempotency_store.close()

    asyncio.run(scenario())
