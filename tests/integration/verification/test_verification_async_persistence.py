from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    AsyncVerificationCompletionAuthorityAdapter,
    AsyncVerificationServiceAdapter,
    CompletionState,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
    VerificationCompletionAuthority,
    VerificationPersistenceOffload,
    VerificationPolicy,
    VerificationRequestStatus,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
    runtime_verification_completion,
)


def _subject(*, result_id: str | None = None) -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=result_id or new_id("result"),
        revision="1",
        digest="sha256:verification-async-persistence",
    )


def _register_policy(
    service: VerificationService,
    *,
    request_timeout_seconds: int | None = None,
) -> VerificationPolicy:
    return service.register_policy(
        VerificationPolicy(
            name="async-persistence",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            request_timeout_seconds=request_timeout_seconds,
        )
    )


async def _wait_for_event(event: threading.Event, *, timeout: float = 1.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    return event.is_set()


class _SlowSqliteVerificationService(SqliteVerificationService):
    def __init__(self, path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


class _SlowSqliteVerificationCompletionAuthority(SqliteVerificationCompletionAuthority):
    def __init__(self, verification: VerificationService, path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(verification, path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


class _BlockingVerificationService(VerificationService):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_reads = False

    def get_request(self, verification_id: str, *, now=None):  # type: ignore[no-untyped-def]
        if self.block_reads:
            self.started.set()
            self.release.wait(timeout=5)
        return super().get_request(verification_id, now=now)


class _FailingVerificationService(VerificationService):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def get_request(self, verification_id: str, *, now=None):  # type: ignore[no-untyped-def]
        del verification_id, now
        raise self.failure


class _BlockingCompletionAuthority(VerificationCompletionAuthority):
    def __init__(self, verification: VerificationService, *, failure: BaseException | None = None):
        super().__init__(verification)
        self.started = threading.Event()
        self.release = threading.Event()
        self.failure = failure

    def request_canonical_verification(self, **kwargs):  # type: ignore[no-untyped-def]
        self.started.set()
        self.release.wait(timeout=5)
        if self.failure is not None:
            raise self.failure
        return super().request_canonical_verification(**kwargs)


def test_sqlite_expiry_write_is_worker_owned_and_event_loop_remains_responsive(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
        policy = _register_policy(service, request_timeout_seconds=1)
        task_id = new_id("task")
        subject = _subject()
        created_at = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)
        request = service.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id=task_id,
            now=created_at,
        )
        service.connection_threads.clear()
        service.delay_seconds = 0.08
        adapter = AsyncVerificationServiceAdapter(service)

        read = asyncio.create_task(
            adapter.get_request(
                request.verification_id,
                now=created_at + timedelta(seconds=2),
            )
        )
        heartbeat = 0
        while not read.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        expired = await read
        assert expired.status is VerificationRequestStatus.EXPIRED
        assert heartbeat >= 2
        assert service.connection_threads
        assert all(
            name.startswith("verification-persistence") for name in service.connection_threads
        )

    asyncio.run(scenario())


def test_completion_assessment_projects_expiry_without_sqlite_write(tmp_path: Path) -> None:
    service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
    completion = SqliteVerificationCompletionAuthority(service, tmp_path / "verification.sqlite3")
    policy = _register_policy(service, request_timeout_seconds=1)
    task_id = new_id("task")
    subject = _subject()
    created_at = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)
    request = completion.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id=task_id,
        now=created_at,
    )

    service.connection_threads.clear()
    service.delay_seconds = 0.08
    decision = service.assess_completion(
        task_id=task_id,
        subject=subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        now=created_at + timedelta(seconds=2),
    )

    assert decision.state is CompletionState.WAITING
    assert decision.blocking_verification_ids == (request.verification_id,)
    assert service.connection_threads == []
    assert service.history(task_id=task_id)[0][0].status is VerificationRequestStatus.PENDING


def test_kernel_output_invalidation_is_worker_owned_and_event_loop_remains_responsive(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "verification.sqlite3"
        service = SqliteVerificationService(path)
        completion = _SlowSqliteVerificationCompletionAuthority(service, path)
        policy = _register_policy(service)
        task_id = new_id("task")
        subject = _subject()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            completion_authority=completion,
            async_completion_authority=runtime_verification_completion(completion),
        )
        await kernel.create_task(
            idempotency_key="verification-async-invalidation:create-task",
            task_id=task_id,
            title="Verification async invalidation",
            objective="Keep SQLite invalidation off the event loop",
            owner_type="user",
            owner_id="verification-async-invalidation-user",
        )
        completion.require_task(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )
        completion.bind_subject(task_id=task_id, subject=subject)
        completion.connection_threads.clear()
        completion.delay_seconds = 0.08

        attachment = asyncio.create_task(
            kernel.attach_result(
                idempotency_key="verification-async-invalidation:attach-result",
                task_id=task_id,
                result_id=new_id("result"),
            )
        )
        heartbeat = 0
        while not attachment.done():
            heartbeat += 1
            await asyncio.sleep(0.005)
        await attachment

        requirement = completion.requirement_for(task_id)
        assert requirement is not None
        assert requirement.subject is None
        assert heartbeat >= 2
        assert completion.connection_threads
        assert all(
            name.startswith("verification-persistence") for name in completion.connection_threads
        )

    asyncio.run(scenario())


def test_persistence_queue_is_bounded_before_executor_submission() -> None:
    async def scenario() -> None:
        service = _BlockingVerificationService()
        policy = _register_policy(service)
        task_id = new_id("task")
        subject = _subject()
        request = service.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id=task_id,
        )
        service.block_reads = True
        offload = VerificationPersistenceOffload(max_concurrency=1, max_pending=2)
        adapter = AsyncVerificationServiceAdapter(service, offload=offload)

        first = asyncio.create_task(adapter.get_request(request.verification_id))
        assert await _wait_for_event(service.started)
        second = asyncio.create_task(adapter.get_request(request.verification_id))
        await asyncio.sleep(0)
        with pytest.raises(ContractError) as captured:
            await adapter.get_request(request.verification_id)
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

        service.release.set()
        assert (await first).verification_id == request.verification_id
        assert (await second).verification_id == request.verification_id

    asyncio.run(scenario())


def test_service_and_completion_adapters_share_one_serialization_boundary() -> None:
    service = VerificationService()
    completion = VerificationCompletionAuthority(service)
    service_adapter = AsyncVerificationServiceAdapter(service)
    completion_adapter = AsyncVerificationCompletionAuthorityAdapter(completion)
    assert service_adapter.offload is completion_adapter.offload


def test_cancellation_waits_for_started_persistence_to_settle() -> None:
    async def scenario() -> None:
        service = VerificationService()
        policy = _register_policy(service)
        completion = _BlockingCompletionAuthority(service)
        adapter = AsyncVerificationCompletionAuthorityAdapter(completion)
        task_id = new_id("task")
        subject = _subject()

        write = asyncio.create_task(
            adapter.request_canonical_verification(
                task_id=task_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                stage_id="review",
                subject=subject,
                result_id=subject.subject_id,
                correlation_id=task_id,
            )
        )
        assert await _wait_for_event(completion.started)
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()

        completion.release.set()
        with pytest.raises(asyncio.CancelledError):
            await write
        requirement = completion.requirement_for(task_id)
        assert requirement is not None
        assert requirement.subject == subject
        assert len(service.history(task_id=task_id)) == 1

    asyncio.run(scenario())


def test_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        service = VerificationService()
        policy = _register_policy(service)
        completion = _BlockingCompletionAuthority(
            service,
            failure=sqlite3.OperationalError("database is locked"),
        )
        adapter = AsyncVerificationCompletionAuthorityAdapter(completion)
        task_id = new_id("task")
        subject = _subject()

        write = asyncio.create_task(
            adapter.request_canonical_verification(
                task_id=task_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                stage_id="review",
                subject=subject,
                result_id=subject.subject_id,
                correlation_id=task_id,
            )
        )
        assert await _wait_for_event(completion.started)
        write.cancel()
        completion.release.set()

        with pytest.raises(ContractError) as captured:
            await write
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (sqlite3.OperationalError("database is locked"), ErrorCode.TRANSIENT_FAILURE, True),
        (sqlite3.DatabaseError("corrupt page"), ErrorCode.BACKEND_ERROR, False),
    ],
)
def test_sqlite_failures_map_to_canonical_contract_errors(
    failure: BaseException,
    code: ErrorCode,
    retryable: bool,
) -> None:
    async def scenario() -> None:
        adapter = AsyncVerificationServiceAdapter(_FailingVerificationService(failure))
        with pytest.raises(ContractError) as captured:
            await adapter.get_request(new_id("verification"))
        assert captured.value.code is code
        assert captured.value.retryable is retryable

    asyncio.run(scenario())


def test_async_completion_request_survives_sqlite_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "verification.sqlite3"
        service = SqliteVerificationService(path)
        completion = SqliteVerificationCompletionAuthority(service, path)
        policy = _register_policy(service)
        adapter = AsyncVerificationCompletionAuthorityAdapter(completion)
        task_id = new_id("task")
        subject = _subject()

        request = await adapter.request_canonical_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id=task_id,
        )
        assert (await adapter.requirement_for(task_id)) is not None
        assert (await adapter.assess_task_completion(task_id)).state is CompletionState.WAITING

        restored_service = SqliteVerificationService(path)
        restored_completion = SqliteVerificationCompletionAuthority(restored_service, path)
        restored_request = restored_service.get_request(request.verification_id)
        restored_requirement = restored_completion.requirement_for(task_id)
        assert restored_request.subject == subject
        assert restored_requirement is not None
        assert restored_requirement.subject == subject

    asyncio.run(scenario())
