from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any

import pytest

from ai_multi_agent_platform.accounting.async_service import AccountingPersistenceOffload
from ai_multi_agent_platform.coordination.async_repository import CoordinationPersistenceOffload
from ai_multi_agent_platform.decisions.async_persistence import DecisionPersistenceOffload
from ai_multi_agent_platform.governance.async_persistence import GovernancePersistenceOffload
from ai_multi_agent_platform.handoffs.async_repository import HandoffPersistenceOffload
from ai_multi_agent_platform.learning.async_persistence import LearningPersistenceOffload
from ai_multi_agent_platform.repositories.async_provenance import (
    RepositoryProvenancePersistenceOffload,
)
from ai_multi_agent_platform.security.async_authentication import AuthenticationPersistenceOffload
from ai_multi_agent_platform.security.async_authorization_policy import (
    AuthorizationPolicyPersistenceOffload,
)
from ai_multi_agent_platform.verification.async_persistence import VerificationPersistenceOffload
from ai_multi_agent_platform.workspaces._retention_async import (
    WorkspaceRetentionPersistenceOffload,
)


@dataclass(frozen=True, slots=True)
class _OffloadCase:
    name: str
    factory: Callable[[], Any]
    requires_message: bool = False


_OFFLOAD_CASES = (
    _OffloadCase(
        "accounting",
        lambda: AccountingPersistenceOffload(max_concurrency=1, max_pending=1),
    ),
    _OffloadCase(
        "coordination",
        lambda: CoordinationPersistenceOffload(max_concurrency=1, max_pending=1),
    ),
    _OffloadCase(
        "decisions",
        lambda: DecisionPersistenceOffload(max_concurrency=1, max_pending=1),
        requires_message=True,
    ),
    _OffloadCase(
        "governance",
        lambda: GovernancePersistenceOffload(max_concurrency=1, max_pending=1),
        requires_message=True,
    ),
    _OffloadCase(
        "handoffs",
        lambda: HandoffPersistenceOffload(max_concurrency=1, max_pending=1),
    ),
    _OffloadCase(
        "learning",
        lambda: LearningPersistenceOffload(max_concurrency=1, max_pending=1),
        requires_message=True,
    ),
    _OffloadCase(
        "repository-provenance",
        lambda: RepositoryProvenancePersistenceOffload(max_concurrency=1, max_pending=1),
        requires_message=True,
    ),
    _OffloadCase(
        "authentication",
        lambda: AuthenticationPersistenceOffload(max_concurrency=1, max_pending=1),
    ),
    _OffloadCase(
        "authorization-policy",
        lambda: AuthorizationPolicyPersistenceOffload(max_concurrency=1, max_pending=1),
    ),
    _OffloadCase(
        "verification",
        lambda: VerificationPersistenceOffload(max_concurrency=1, max_pending=1),
    ),
    _OffloadCase(
        "workspace-retention",
        lambda: WorkspaceRetentionPersistenceOffload(max_pending=1),
        requires_message=True,
    ),
)


class _RaisingExecutor(Executor):
    def __init__(self, failure: BaseException) -> None:
        self._failure = failure

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        del fn, args, kwargs
        raise self._failure


async def _run(
    case: _OffloadCase,
    offload: Any,
    operation: Callable[[], object],
) -> object:
    run: Callable[..., Awaitable[object]] = offload.run
    if case.requires_message:
        return await run(operation, message="persistence ownership regression")
    return await run(operation)


@pytest.mark.parametrize("case", _OFFLOAD_CASES, ids=lambda case: case.name)
@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
def test_submission_process_signal_releases_untransferred_capacity(
    case: _OffloadCase,
    signal_type: type[BaseException],
) -> None:
    async def scenario() -> None:
        offload = case.factory()
        offload._executor.shutdown(wait=True)
        signal = signal_type("process-control during executor submission")
        offload._executor = _RaisingExecutor(signal)

        with pytest.raises(signal_type) as caught:
            await _run(case, offload, lambda: None)

        assert caught.value is signal
        assert offload._capacity.acquire(blocking=False)
        offload._capacity.release()

    asyncio.run(scenario())


@pytest.mark.parametrize("case", _OFFLOAD_CASES, ids=lambda case: case.name)
def test_started_worker_failure_wins_pending_repeated_cancellation(case: _OffloadCase) -> None:
    async def scenario() -> None:
        offload = case.factory()
        started = threading.Event()
        release = threading.Event()
        failure = RuntimeError(f"{case.name} worker failure")

        def operation() -> object:
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("worker release was not signalled")
            raise failure

        try:
            task = asyncio.create_task(_run(case, offload, operation))
            while not started.is_set():
                await asyncio.sleep(0)

            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()

            with pytest.raises(RuntimeError) as caught:
                await task

            assert caught.value is failure
            assert offload._capacity.acquire(blocking=False)
            offload._capacity.release()
        finally:
            release.set()
            offload._executor.shutdown(wait=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("case", _OFFLOAD_CASES, ids=lambda case: case.name)
def test_started_worker_success_preserves_pending_cancellation(case: _OffloadCase) -> None:
    async def scenario() -> None:
        offload = case.factory()
        started = threading.Event()
        release = threading.Event()

        def operation() -> object:
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("worker release was not signalled")
            return "settled"

        try:
            task = asyncio.create_task(_run(case, offload, operation))
            while not started.is_set():
                await asyncio.sleep(0)

            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()

            with pytest.raises(asyncio.CancelledError):
                await task

            assert offload._capacity.acquire(blocking=False)
            offload._capacity.release()
        finally:
            release.set()
            offload._executor.shutdown(wait=True)

    asyncio.run(scenario())
