from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.config import load_single_node_config
from ai_multi_agent_platform.deployment.drain import (
    SingleNodeDrainController,
    SingleNodeDrainState,
)
from ai_multi_agent_platform.deployment.startup_recovery import reconcile_single_node_startup
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry


def test_shutdown_timeout_is_explicit_single_node_configuration(tmp_path: Path) -> None:
    config = load_single_node_config(
        {
            "AI_MAP_DATA_DIR": str(tmp_path / "data"),
            "AI_MAP_SECURE_COOKIE": "false",
            "AI_MAP_SHUTDOWN_TIMEOUT_SECONDS": "17",
        }
    )
    assert config.shutdown_timeout_seconds == 17


def test_drain_rejects_mutations_and_projects_health_readiness(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "drain-http",
                secure_cookie=False,
                shutdown_timeout_seconds=5,
            )
        )

        ready = await deployment.http.handle(
            HTTPRequest(method="GET", path="/api/v1/readiness")
        )
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

        assert openapi.status == 200

        assert blocked.status == 503
        assert isinstance(blocked.body, dict)
        assert blocked.body["code"] == "unavailable"
        assert blocked.body["retryable"] is True
        assert blocked.body["details"]["draining"] is True

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

    asyncio.run(scenario())


def test_process_local_drain_state_is_not_revived_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "restart"
        first = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
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


def test_lifespan_teardown_timeout_cannot_hang_process_exit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "teardown-timeout",
                secure_cookie=False,
                shutdown_timeout_seconds=1,
            )
        )
        # Keep the integration test fast while exercising the exact production deadline path.
        deployment.drain.timeout_seconds = 0.05
        never = asyncio.Event()

        async def stuck_notification_shutdown() -> None:
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
                raise AssertionError("unreachable")

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

    asyncio.run(scenario())
