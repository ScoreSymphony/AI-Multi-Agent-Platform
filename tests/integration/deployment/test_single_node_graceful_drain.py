from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.configuration import ConfigurationError
from ai_multi_agent_platform.control_plane import ControlPlaneASGI, HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.config import (
    MAX_SHUTDOWN_TIMEOUT_SECONDS,
    load_single_node_config,
)
from ai_multi_agent_platform.deployment.drain import (
    SingleNodeDrainController,
    SingleNodeDrainState,
)
from ai_multi_agent_platform.deployment.startup_recovery import reconcile_single_node_startup
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def test_shutdown_timeout_is_explicit_single_node_configuration(tmp_path: Path) -> None:
    config = load_single_node_config(
        {
            "AI_MAP_DATA_DIR": str(tmp_path / "data"),
            "AI_MAP_SECURE_COOKIE": "false",
            "AI_MAP_SHUTDOWN_TIMEOUT_SECONDS": "17",
        }
    )
    assert config.shutdown_timeout_seconds == 17


def test_shutdown_timeout_supported_range_has_explicit_upper_bound(tmp_path: Path) -> None:
    common = {
        "AI_MAP_DATA_DIR": str(tmp_path / "data"),
        "AI_MAP_SECURE_COOKIE": "false",
    }
    config = load_single_node_config(
        {
            **common,
            "AI_MAP_SHUTDOWN_TIMEOUT_SECONDS": str(MAX_SHUTDOWN_TIMEOUT_SECONDS),
        }
    )
    assert config.shutdown_timeout_seconds == MAX_SHUTDOWN_TIMEOUT_SECONDS

    with pytest.raises(ConfigurationError):
        load_single_node_config(
            {
                **common,
                "AI_MAP_SHUTDOWN_TIMEOUT_SECONDS": str(MAX_SHUTDOWN_TIMEOUT_SECONDS + 1),
            }
        )


def test_drain_rejects_mutations_and_projects_health_readiness(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "drain-http",
                secure_cookie=False,
                shutdown_timeout_seconds=5,
            )
        )

        ready = await deployment.http.handle(HTTPRequest(method="GET", path="/api/v1/readiness"))
        assert ready.status == 200
        assert isinstance(ready.body, dict)
        assert ready.body["ready"] is True

        await deployment.drain.begin(reason="test_shutdown")

        health = await deployment.http.handle(HTTPRequest(method="GET", path="/api/v1/health"))
        readiness = await deployment.http.handle(
            HTTPRequest(method="GET", path="/api/v1/readiness")
        )
        openapi = await deployment.http.handle(
            HTTPRequest(method="GET", path="/api/v1/openapi.json")
        )
        blocked = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={"content-type": "application/json"},
                body={
                    "title": "must not be admitted",
                    "objective": "prove drain admission is closed",
                },
            )
        )

        assert health.status == 200
        assert isinstance(health.body, dict)
        assert health.body["status"] == "draining"
        assert health.body["ready"] is False
        assert health.body["draining"] is True

        assert readiness.status == 503
        assert isinstance(readiness.body, dict)
        assert readiness.body["status"] == "draining"
        assert readiness.body["ready"] is False

        canonical_health = await deployment.control_plane.health()
        assert canonical_health["ready"] is False

        assert openapi.status == 200

        assert blocked.status == 503
        assert isinstance(blocked.body, dict)
        assert blocked.body["code"] == "unavailable"
        assert blocked.body["retryable"] is True
        assert blocked.body["details"]["draining"] is True

    asyncio.run(scenario())


def test_drain_rejects_direct_asgi_streaming_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "drain-asgi-stream",
                secure_cookie=False,
                shutdown_timeout_seconds=1,
            )
        )
        await deployment.drain.begin(reason="test_direct_asgi_mutation")

        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await deployment.app(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/conversation-messages/message_test/response/stream",
                "headers": [],
                "query_string": b"",
            },
            receive,
            send,
        )

        response_start = next(
            message for message in sent if message["type"] == "http.response.start"
        )
        assert response_start["status"] == 503
        assert deployment.drain.active_mutations == 0

    asyncio.run(scenario())


def test_drain_rejects_new_websocket_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "drain-websocket",
                secure_cookie=False,
                shutdown_timeout_seconds=1,
            )
        )
        await deployment.drain.begin(reason="test_websocket_admission")

        received = False
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal received
            assert received is False
            received = True
            return {"type": "websocket.connect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await deployment.app(
            {
                "type": "websocket",
                "path": "/api/v1/terminal/sessions/session_test/stream",
                "headers": [],
                "query_string": b"",
            },
            receive,
            send,
        )

        assert sent == [
            {
                "type": "websocket.close",
                "code": 1013,
                "reason": "single-node Control Plane is draining",
            }
        ]
        assert deployment.drain.active_mutations == 0

    asyncio.run(scenario())


def test_already_admitted_mutation_may_settle_within_deadline() -> None:
    async def scenario() -> None:
        exporter = InMemoryExporter()
        drain = SingleNodeDrainController(
            timeout_seconds=1,
            telemetry=Telemetry(exporter),
        )
        assert await drain.try_admit_mutation() is True
        await drain.begin(reason="test")

        waiter = asyncio.create_task(drain.wait_for_inflight())
        await asyncio.sleep(0)
        assert waiter.done() is False

        await drain.release_mutation()
        assert await waiter is True
        assert drain.snapshot().forced is False

        names = [entry.event_name for entry in exporter.timeline]
        assert "platform.single_node.drain.requested" in names
        assert "platform.single_node.drain.entered" in names

    asyncio.run(scenario())


def test_drain_quiesces_autonomous_background_runtimes(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "autonomous-quiesce",
                secure_cookie=False,
                shutdown_timeout_seconds=1,
            )
        )
        await deployment.control_plane.start_automation_runtime()
        await deployment.control_plane.start_notification_runtime()
        assert deployment.control_plane.automation_runtime.running is True
        assert deployment.control_plane.notification_runtime.running is True

        await deployment.drain.begin(reason="test_autonomous_quiesce")
        await deployment.control_plane.stop_notification_runtime()
        await deployment.control_plane.stop_automation_runtime()

        assert deployment.control_plane.automation_runtime.running is False
        assert deployment.control_plane.notification_runtime.running is False

    asyncio.run(scenario())


def test_drain_lifecycle_does_not_depend_on_telemetry_exporter() -> None:
    class FailingExporter(InMemoryExporter):
        def emit_log(self, record: Any) -> None:
            del record
            raise RuntimeError("log exporter unavailable")

        def emit_metric(self, record: Any) -> None:
            del record
            raise RuntimeError("metric exporter unavailable")

        def emit_span(self, record: Any) -> None:
            del record
            raise RuntimeError("span exporter unavailable")

        def emit_timeline(self, record: Any) -> None:
            del record
            raise RuntimeError("timeline exporter unavailable")

    async def scenario() -> None:
        drain = SingleNodeDrainController(
            timeout_seconds=1,
            telemetry=Telemetry(
                FailingExporter(),
                strict_exporter_errors=True,
            ),
        )
        assert await drain.begin(reason="telemetry_failure") is True
        await drain.mark_forced("teardown_failure")
        await drain.mark_completed()

        snapshot = drain.snapshot()
        assert snapshot.state is SingleNodeDrainState.DRAINING
        assert snapshot.forced is True
        assert snapshot.completed is True
        assert snapshot.force_reason == "teardown_failure"

    asyncio.run(scenario())


def test_non_timeout_forced_drain_reports_failed_completion() -> None:
    async def scenario() -> None:
        exporter = InMemoryExporter()
        drain = SingleNodeDrainController(
            timeout_seconds=1,
            telemetry=Telemetry(exporter),
        )
        await drain.begin(reason="test_failure")
        await drain.mark_forced("resource_teardown_failure")
        await drain.mark_completed()

        completed = next(
            entry
            for entry in exporter.timeline
            if entry.event_name == "platform.single_node.drain.completed"
        )
        assert completed.outcome.value == "failed"

    asyncio.run(scenario())


def test_drain_timeout_is_forced_and_observable() -> None:
    async def scenario() -> None:
        exporter = InMemoryExporter()
        drain = SingleNodeDrainController(
            timeout_seconds=0.01,
            telemetry=Telemetry(exporter),
        )
        assert await drain.try_admit_mutation() is True
        await drain.begin(reason="test_timeout")

        assert await drain.wait_for_inflight() is False
        snapshot = drain.snapshot()
        assert snapshot.state is SingleNodeDrainState.DRAINING
        assert snapshot.forced is True
        assert snapshot.force_reason == "in_flight_mutation_timeout"

        await drain.release_mutation()
        await drain.mark_completed()

        names = [entry.event_name for entry in exporter.timeline]
        assert "platform.single_node.drain.timeout" in names
        assert "platform.single_node.drain.completed" in names
        completed = next(
            entry
            for entry in exporter.timeline
            if entry.event_name == "platform.single_node.drain.completed"
        )
        assert completed.outcome.value == "timed_out"

    asyncio.run(scenario())


def test_process_local_drain_state_is_not_revived_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "restart"
        first = build_single_node_deployment(SingleNodeConfig(data_dir=root, secure_cookie=False))
        await first.drain.begin(reason="first_process_shutdown")
        assert first.drain.draining is True

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        assert restarted.drain.draining is False

        recovery = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )
        assert recovery.ready_for_service is True
        assert recovery.unresolved_run_ids == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("forced", [False, True], ids=["graceful", "forced"])
def test_drain_preserves_running_run_for_canonical_restart_recovery(
    tmp_path: Path,
    forced: bool,
) -> None:
    async def scenario() -> None:
        root = tmp_path / ("forced-running-run" if forced else "graceful-running-run")
        (root / "db").mkdir(parents=True)
        (root / "files").mkdir()
        (root / "workspaces").mkdir()

        original = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=SqliteKernelRepository(root / "db" / "kernel.sqlite3"),
        )
        task = await original.create_task(
            idempotency_key="drain:create",
            title="Interrupted work",
            objective="Remain canonical across process teardown",
            owner_type="service",
            owner_id="graceful-drain-test",
        )
        await original.ready_task(idempotency_key="drain:ready", task_id=task.task_id)
        running = await original.start_task(
            idempotency_key="drain:start",
            task_id=task.task_id,
        )
        assert running.status.value == "running"

        stopping = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        reason = "test_forced_shutdown" if forced else "test_graceful_shutdown"
        await stopping.drain.begin(reason=reason)
        if forced:
            await stopping.drain.mark_forced(reason, timed_out=True)
        await stopping.drain.mark_completed()
        assert stopping.drain.snapshot().forced is forced

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        recovery = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )
        recovered = await restarted.kernel.get_run(task.task_id, running.run_id)

        assert recovery.ready_for_service is False
        assert recovery.unresolved_run_ids == (running.run_id,)
        assert recovered.run_id == running.run_id
        assert recovered.status.value == "running"
        assert recovered.recovery_required is True
        assert recovered.recovery_reason == "canonical_running_backend_not_found"

        repeated = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )
        assert repeated.unresolved_run_ids == (running.run_id,)
        repeated_run = await restarted.kernel.get_run(task.task_id, running.run_id)
        assert repeated_run.run_id == running.run_id

    asyncio.run(scenario())


def test_open_websocket_session_cannot_hold_shutdown_past_drain_deadline(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "stuck-websocket",
                secure_cookie=False,
                shutdown_timeout_seconds=1,
            )
        )
        deployment.drain.timeout_seconds = 0.05
        websocket_entered = asyncio.Event()
        never = asyncio.Event()
        original_call = ControlPlaneASGI.__call__

        async def hold_websocket(
            app: ControlPlaneASGI,
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            if scope.get("type") == "websocket":
                websocket_entered.set()
                await never.wait()
                return
            await original_call(app, scope, receive, send)

        monkeypatch.setattr(ControlPlaneASGI, "__call__", hold_websocket)

        async def websocket_receive() -> dict[str, Any]:
            return {"type": "websocket.connect"}

        async def websocket_send(message: dict[str, Any]) -> None:
            del message

        websocket_task = asyncio.create_task(
            deployment.app(
                {
                    "type": "websocket",
                    "path": "/api/v1/terminal/sessions/session_test/stream",
                    "headers": [],
                    "query_string": b"",
                },
                websocket_receive,
                websocket_send,
            )
        )
        await websocket_entered.wait()
        assert deployment.drain.active_mutations == 1

        messages = iter(
            (
                {"type": "lifespan.startup"},
                {"type": "lifespan.shutdown"},
            )
        )
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            try:
                return next(messages)
            except StopIteration:
                await asyncio.Future()
                raise AssertionError("unreachable") from None

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await deployment.app(
            {"type": "lifespan", "asgi": {"version": "3.0"}},
            receive,
            send,
        )

        assert any(item.get("type") == "lifespan.shutdown.complete" for item in sent)
        assert deployment.drain.snapshot().forced is True
        assert deployment.drain.snapshot().force_reason == "in_flight_mutation_timeout"

        websocket_task.cancel()
        try:
            await websocket_task
        except asyncio.CancelledError:
            pass
        assert deployment.drain.active_mutations == 0

    asyncio.run(scenario())


def test_lifespan_teardown_timeout_cannot_hang_process_exit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        exporter = InMemoryExporter()
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "teardown-timeout",
                secure_cookie=False,
                shutdown_timeout_seconds=1,
            ),
            observability_exporter=exporter,
        )
        # Keep the integration test fast while exercising the exact production deadline path.
        deployment.drain.timeout_seconds = 0.05
        never = asyncio.Event()
        first_cancellation_seen = asyncio.Event()

        async def stuck_notification_shutdown() -> None:
            try:
                await never.wait()
            except asyncio.CancelledError:
                first_cancellation_seen.set()
                await never.wait()

        monkeypatch.setattr(
            deployment.control_plane,
            "stop_notification_runtime",
            stuck_notification_shutdown,
        )

        messages = iter(
            (
                {"type": "lifespan.startup"},
                {"type": "lifespan.shutdown"},
            )
        )
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            try:
                return next(messages)
            except StopIteration:
                await asyncio.Future()
                raise AssertionError("unreachable") from None

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await deployment.app(
            {"type": "lifespan", "asgi": {"version": "3.0"}},
            receive,
            send,
        )

        assert any(item.get("type") == "lifespan.startup.complete" for item in sent)
        assert any(item.get("type") == "lifespan.shutdown.complete" for item in sent)
        assert deployment.drain.snapshot().forced is True
        assert deployment.drain.snapshot().completed is True
        assert deployment.drain.snapshot().force_reason == "resource_teardown_timeout"
        assert first_cancellation_seen.is_set() is True

        timeline = {
            entry.event_name: entry
            for entry in exporter.timeline
            if entry.event_name.startswith("platform.single_node.drain.")
        }
        assert timeline["platform.single_node.drain.timeout"].outcome.value == "timed_out"
        assert timeline["platform.single_node.drain.completed"].outcome.value == "timed_out"

    asyncio.run(scenario())
