from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.startup_recovery import reconcile_single_node_startup
from ai_multi_agent_platform.domain import RunStatus


async def _admitted_operation(deployment: Any, operation: Any) -> Any:
    assert await deployment.drain.try_admit_mutation() is True
    try:
        return await operation()
    finally:
        await deployment.drain.release_mutation()


async def _reconcile(deployment: Any) -> Any:
    return await reconcile_single_node_startup(
        data_dir=deployment.config.data_dir,
        kernel=deployment.kernel,
        coordinator=deployment.coordination,
        distributed_runtime=deployment.distributed_runtime,
        extensions=deployment.startup_recovery_extensions,
        reviewer_reconciler=deployment.reviewer_recovery,
    )


def test_terminal_callback_persistence_started_before_drain_settles_once(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "callback-persistence"
        first = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False, shutdown_timeout_seconds=1)
        )
        task = await first.kernel.create_task(
            idempotency_key="callback-race:create",
            title="Callback persistence race",
            objective="Persist one terminal callback while drain starts",
            owner_type="service",
            owner_id="drain-race-test",
        )
        await first.kernel.ready_task(
            idempotency_key="callback-race:ready",
            task_id=task.task_id,
        )
        run = await first.kernel.start_task(
            idempotency_key="callback-race:start",
            task_id=task.task_id,
        )

        entered_commit = asyncio.Event()
        release_commit = asyncio.Event()
        original_commit = first.kernel._commit_task_command

        async def blocked_commit(**kwargs: Any) -> Any:
            if kwargs.get("operation") == "record_run_outcome":
                entered_commit.set()
                await release_commit.wait()
            return await original_commit(**kwargs)

        monkeypatch.setattr(first.kernel, "_commit_task_command", blocked_commit)

        async def persist_callback() -> Any:
            return await first.kernel.record_run_outcome(
                idempotency_key="callback-race:terminal",
                task_id=task.task_id,
                run_id=run.run_id,
                status=RunStatus.SUCCEEDED,
                output={"answer": "persisted"},
            )

        callback_task = asyncio.create_task(
            _admitted_operation(first, persist_callback),
            name="drain-callback-persistence",
        )
        await entered_commit.wait()
        await first.drain.begin(reason="callback_persistence_race")
        drain_waiter = asyncio.create_task(first.drain.wait_for_inflight())
        await asyncio.sleep(0)
        assert drain_waiter.done() is False

        release_commit.set()
        completed = await callback_task
        assert completed.status is RunStatus.SUCCEEDED
        assert await drain_waiter is True

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        recovery = await _reconcile(restarted)
        restored = await restarted.kernel.get_run(task.task_id, run.run_id)
        assert recovery.ready_for_service is True
        assert restored.run_id == run.run_id
        assert restored.status is RunStatus.SUCCEEDED
        assert restored.output == {"answer": "persisted"}

        duplicate = await restarted.kernel.record_run_outcome(
            idempotency_key="callback-race:terminal",
            task_id=task.task_id,
            run_id=run.run_id,
            status=RunStatus.SUCCEEDED,
            output={"answer": "persisted"},
        )
        assert duplicate.run_id == run.run_id
        history = await restarted.kernel.history(task.task_id)
        assert [event.event_type for event in history].count("run.succeeded") == 1

    asyncio.run(scenario())


def test_cancellation_started_before_drain_settles_without_duplicate_run(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "cancellation-race"
        first = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False, shutdown_timeout_seconds=1)
        )
        task = await first.kernel.create_task(
            idempotency_key="cancel-race:create",
            title="Cancellation drain race",
            objective="Settle one admitted cancellation while drain starts",
            owner_type="service",
            owner_id="drain-race-test",
        )
        await first.kernel.ready_task(
            idempotency_key="cancel-race:ready",
            task_id=task.task_id,
        )
        run = await first.kernel.start_task(
            idempotency_key="cancel-race:start",
            task_id=task.task_id,
        )

        entered_cancel = asyncio.Event()
        release_cancel = asyncio.Event()
        original_finish_cancel = first.kernel._finish_cancel

        async def blocked_finish_cancel(*args: Any, **kwargs: Any) -> Any:
            entered_cancel.set()
            await release_cancel.wait()
            return await original_finish_cancel(*args, **kwargs)

        monkeypatch.setattr(first.kernel, "_finish_cancel", blocked_finish_cancel)

        async def cancel() -> Any:
            return await first.kernel.cancel_run(
                idempotency_key="cancel-race:cancel",
                task_id=task.task_id,
                run_id=run.run_id,
            )

        cancel_task = asyncio.create_task(
            _admitted_operation(first, cancel),
            name="drain-cancellation-race",
        )
        await entered_cancel.wait()
        await first.drain.begin(reason="cancellation_race")
        drain_waiter = asyncio.create_task(first.drain.wait_for_inflight())
        await asyncio.sleep(0)
        assert drain_waiter.done() is False

        release_cancel.set()
        cancelled = await cancel_task
        assert cancelled.status is RunStatus.CANCELLED
        assert await drain_waiter is True

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        recovery = await _reconcile(restarted)
        restored = await restarted.kernel.get_run(task.task_id, run.run_id)
        assert recovery.ready_for_service is True
        assert restored.run_id == run.run_id
        assert restored.status is RunStatus.CANCELLED

        repeated = await restarted.kernel.cancel_run(
            idempotency_key="cancel-race:cancel",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        assert repeated.run_id == run.run_id
        history = await restarted.kernel.history(task.task_id)
        event_types = [event.event_type for event in history]
        assert event_types.count("run.created") == 1
        assert event_types.count("run.cancelled") == 1

    asyncio.run(scenario())
