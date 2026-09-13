"""Async Evaluation persistence regressions for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.evaluation_contract import EvaluationRunResourceService
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRun,
    EvaluationRunner,
    EvaluationRunStatus,
    EvaluationService,
    EvaluationSuite,
    InMemoryEvaluationRepository,
    SqliteEvalManifestRepository,
    SqliteEvaluationRepository,
)
from ai_multi_agent_platform.evaluation.async_persistence import (
    AsyncEvalManifestRepositoryAdapter,
    AsyncEvaluationHistoryRepositoryAdapter,
    AsyncEvaluationSuiteAssetRepositoryAdapter,
    EvaluationPersistenceOffload,
)
from ai_multi_agent_platform.evaluation.product import TargetAwareEvaluationService
from ai_multi_agent_platform.evaluation.reproducibility import EvalManifestBuilder
from ai_multi_agent_platform.evaluation.suite_assets import (
    SqliteEvaluationSuiteAssetRepository,
    suite_ref,
)
from ai_multi_agent_platform.portability.evaluation_import import (
    EvaluationSuiteImportMutationHandler,
    EvaluationSuiteImportToken,
)


class _StaticExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return EvaluationObservation(data={"result": {"status": "ok"}})


class _IdentityTargetEnricher:
    def enrich(
        self,
        suite: EvaluationSuite,
        snapshot: ConfigurationSnapshot,
    ) -> ConfigurationSnapshot:
        del suite
        return snapshot


class _RecordingHistoryRepository(SqliteEvaluationRepository):
    def __init__(self, path: Path, *, delay: float = 0.05) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        self._delay = delay
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(self._delay)
        return super()._connect()


class _BusyHistoryRepository(SqliteEvaluationRepository):
    def __init__(self, path: Path) -> None:
        self.busy = False
        super().__init__(path)
        self.busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


class _BlockingSuiteRepository(SqliteEvaluationSuiteAssetRepository):
    def __init__(self, path: Path) -> None:
        self.block_connections = False
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)
        self.block_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.block_connections:
            self.started.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test evaluation persistence worker release timed out")
        return super()._connect()


class _SlowSuiteRepository(SqliteEvaluationSuiteAssetRepository):
    def __init__(self, path: Path) -> None:
        self.delay_connections = False
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        if self.delay_connections:
            time.sleep(0.06)
        return super()._connect()


class _WriteTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.04)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


class _TrackedHistoryRepository(SqliteEvaluationRepository):
    def __init__(self, path: Path, tracker: _WriteTracker) -> None:
        self._tracker = tracker
        super().__init__(path)

    def save_run(self, run: EvaluationRun) -> None:
        self._tracker.enter()
        try:
            super().save_run(run)
        finally:
            self._tracker.leave()


class _TrackedManifestRepository(SqliteEvalManifestRepository):
    def __init__(self, path: Path, tracker: _WriteTracker) -> None:
        self._tracker = tracker
        super().__init__(path)

    def save_manifest(self, manifest: Any) -> None:
        self._tracker.enter()
        try:
            super().save_manifest(manifest)
        finally:
            self._tracker.leave()


class _TrackedSuiteRepository(SqliteEvaluationSuiteAssetRepository):
    def __init__(self, path: Path, tracker: _WriteTracker) -> None:
        self._tracker = tracker
        super().__init__(path)

    def create_suite(self, suite: EvaluationSuite) -> str:
        self._tracker.enter()
        try:
            return super().create_suite(suite)
        finally:
            self._tracker.leave()


class _AsyncOnlyEvaluationService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_suite(self, suite_reference: str) -> EvaluationSuite:
        del suite_reference
        raise AssertionError("sync get_suite must not be used by async portability")

    def create_suite(self, suite: EvaluationSuite) -> str:
        del suite
        raise AssertionError("sync create_suite must not be used by async portability")

    def delete_suite(self, suite_reference: str, *, expected_checksum: str | None = None) -> None:
        del suite_reference, expected_checksum
        raise AssertionError("sync delete_suite must not be used by async portability")

    async def get_suite_async(self, suite_reference: str) -> EvaluationSuite:
        self.calls.append("get")
        raise ContractError(ErrorCode.NOT_FOUND, f"missing: {suite_reference}")

    async def create_suite_async(self, suite: EvaluationSuite) -> str:
        self.calls.append("create")
        assert suite == _suite()
        return "sha256:test"

    async def delete_suite_async(
        self,
        suite_reference: str,
        *,
        expected_checksum: str | None = None,
    ) -> None:
        self.calls.append("delete")
        assert suite_reference == suite_ref(_suite())
        assert expected_checksum == "sha256:test"


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="issue-892.async-evaluation",
        name="Issue 892 async Evaluation",
        version="1.0",
        cases=(
            EvaluationCase(
                case_id="issue-892.async-evaluation.case",
                name="Async persistence case",
                version="1.0",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="status-ok",
                        path="result.status",
                        operator=ComparisonOperator.EQ,
                        expected="ok",
                    ),
                ),
            ),
        ),
    )


def _snapshot(version: str = "test") -> ConfigurationSnapshot:
    return ConfigurationSnapshot(platform_version=version)


def _run(*, status: EvaluationRunStatus = EvaluationRunStatus.RUNNING) -> EvaluationRun:
    suite = _suite()
    run = EvaluationRun(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        snapshot=_snapshot(),
        status=EvaluationRunStatus.RUNNING,
    )
    if status is EvaluationRunStatus.RUNNING:
        return run
    if status is EvaluationRunStatus.COMPLETED:
        return replace(run, status=status, completed_at=run.started_at)
    return replace(run, status=status)


def test_evaluation_sqlite_runtime_is_responsive_and_worker_owned(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RecordingHistoryRepository(tmp_path / "evaluation.sqlite3")
        adapter = AsyncEvaluationHistoryRepositoryAdapter(repository)
        event_loop_thread = threading.get_ident()

        pending = asyncio.create_task(adapter.list_runs())
        await asyncio.sleep(0.01)

        assert not pending.done()
        await pending
        runtime_threads = repository.connection_threads[1:]
        assert runtime_threads
        assert all(thread_id != event_loop_thread for thread_id in runtime_threads)

    asyncio.run(scenario())


def test_evaluation_persistence_concurrency_is_bounded() -> None:
    async def scenario() -> None:
        offload = EvaluationPersistenceOffload(max_concurrency=2)
        counter_lock = threading.Lock()
        active = 0
        max_active = 0

        def operation() -> int:
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.04)
                return threading.get_ident()
            finally:
                with counter_lock:
                    active -= 1

        threads = await asyncio.gather(*(offload.run(operation) for _ in range(8)))
        assert len(set(threads)) <= 2
        assert 1 < max_active <= 2

    asyncio.run(scenario())


def test_evaluation_backlog_does_not_exhaust_default_executor() -> None:
    async def scenario() -> None:
        offload = EvaluationPersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def blocked() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Evaluation worker release timed out")

        pending = [asyncio.create_task(offload.run(blocked)) for _ in range(12)]
        assert await asyncio.to_thread(started.wait, 1)

        try:
            default_worker = await asyncio.wait_for(
                asyncio.to_thread(threading.get_ident),
                timeout=0.5,
            )
            assert default_worker != threading.get_ident()
            assert any(not task.done() for task in pending)
        finally:
            release.set()

        await asyncio.gather(*pending)

    asyncio.run(scenario())


def test_evaluation_shared_write_serialization_spans_all_sqlite_stores(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "evaluation.sqlite3"
        tracker = _WriteTracker()
        history = _TrackedHistoryRepository(database, tracker)
        manifests = _TrackedManifestRepository(database, tracker)
        suites = _TrackedSuiteRepository(database, tracker)
        offload = EvaluationPersistenceOffload(max_concurrency=3)
        history_async = AsyncEvaluationHistoryRepositoryAdapter(history, offload=offload)
        manifest_async = AsyncEvalManifestRepositoryAdapter(manifests, offload=offload)
        suite_async = AsyncEvaluationSuiteAssetRepositoryAdapter(suites, offload=offload)
        run = _run()
        manifest = EvalManifestBuilder().build(run=run, suite=_suite())

        await asyncio.gather(
            history_async.save_run(run),
            manifest_async.save_manifest(manifest),
            suite_async.create_suite(_suite()),
        )

        assert tracker.max_active == 1

    asyncio.run(scenario())


def test_evaluation_runtime_survives_multiple_event_loop_lifetimes(tmp_path: Path) -> None:
    repository = SqliteEvaluationRepository(tmp_path / "evaluation.sqlite3")
    adapter = AsyncEvaluationHistoryRepositoryAdapter(
        repository,
        offload=EvaluationPersistenceOffload(max_concurrency=1),
    )
    run = _run()

    asyncio.run(adapter.save_run(run))
    assert asyncio.run(adapter.get_run(run.run_id)) == run
    assert asyncio.run(adapter.list_runs()) == (run,)


def test_evaluation_cancellation_waits_for_sqlite_write_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "evaluation.sqlite3"
        repository = _BlockingSuiteRepository(database)
        adapter = AsyncEvaluationSuiteAssetRepositoryAdapter(repository)
        suite = _suite()
        pending = asyncio.create_task(adapter.create_suite(suite))
        assert await asyncio.to_thread(repository.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restarted = SqliteEvaluationSuiteAssetRepository(database)
        assert restarted.get_suite(suite_ref(suite)) == suite

    asyncio.run(scenario())


def test_evaluation_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        offload = EvaluationPersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def fail_after_release() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Evaluation worker release timed out")
            raise RuntimeError("worker persistence failed")

        pending = asyncio.create_task(offload.run(fail_after_release, write=True))
        assert await asyncio.to_thread(started.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        release.set()

        with pytest.raises(RuntimeError, match="worker persistence failed"):
            await pending

    asyncio.run(scenario())


def test_evaluation_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BusyHistoryRepository(tmp_path / "evaluation.sqlite3")
        adapter = AsyncEvaluationHistoryRepositoryAdapter(repository)

        with pytest.raises(ContractError) as raised:
            await adapter.list_runs()

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_evaluation_manifest_conflict_semantics_survive_async_facade(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SqliteEvalManifestRepository(tmp_path / "evaluation.sqlite3")
        adapter = AsyncEvalManifestRepositoryAdapter(repository)
        run = _run()
        first = EvalManifestBuilder().build(run=run, suite=_suite())
        changed_run = replace(run, snapshot=_snapshot("different"))
        conflicting = EvalManifestBuilder().build(run=changed_run, suite=_suite())

        await adapter.save_manifest(first)
        with pytest.raises(ContractError) as raised:
            await adapter.save_manifest(conflicting)

        assert raised.value.code is ErrorCode.CONFLICT
        assert await adapter.get_manifest(run.run_id) == first

    asyncio.run(scenario())


def test_evaluation_suite_delete_remains_conflicted_when_run_references_asset(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "evaluation.sqlite3"
        history = SqliteEvaluationRepository(database)
        assets = SqliteEvaluationSuiteAssetRepository(database)
        offload = EvaluationPersistenceOffload(max_concurrency=2)
        history_async = AsyncEvaluationHistoryRepositoryAdapter(history, offload=offload)
        assets_async = AsyncEvaluationSuiteAssetRepositoryAdapter(assets, offload=offload)
        suite = _suite()
        checksum = await assets_async.create_suite(suite)
        run = _run()
        await history_async.save_run(run)

        with pytest.raises(ContractError) as raised:
            await assets_async.delete_suite(
                suite_ref(suite),
                expected_checksum=checksum,
            )

        assert raised.value.code is ErrorCode.CONFLICT
        assert await assets_async.get_suite(suite_ref(suite)) == suite

    asyncio.run(scenario())


def test_failed_suite_write_preserves_existing_durable_asset(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "evaluation.sqlite3"
        adapter = AsyncEvaluationSuiteAssetRepositoryAdapter(
            SqliteEvaluationSuiteAssetRepository(database)
        )
        suite = _suite()
        checksum = await adapter.create_suite(suite)

        with pytest.raises(ContractError) as raised:
            await adapter.create_suite(suite)

        assert raised.value.code is ErrorCode.CONFLICT
        assert await adapter.get_suite(suite_ref(suite)) == suite
        restarted = SqliteEvaluationSuiteAssetRepository(database)
        assert restarted.get_suite(suite_ref(suite)) == suite
        assert checksum.startswith("sha256:")

    asyncio.run(scenario())


def test_evaluation_runner_sqlite_and_in_memory_runtime_semantics_match(tmp_path: Path) -> None:
    async def run_with(repository: Any, manifest_repository: Any = None) -> Any:
        runner = EvaluationRunner(
            repository=repository,
            manifest_repository=manifest_repository,
            executor=_StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        return await runner.run_suite(suite=_suite(), snapshot=_snapshot())

    memory = asyncio.run(run_with(InMemoryEvaluationRepository()))
    database = tmp_path / "evaluation.sqlite3"
    sqlite_repository = SqliteEvaluationRepository(database)
    sqlite = asyncio.run(run_with(sqlite_repository, SqliteEvalManifestRepository(database)))

    assert memory.run.status is EvaluationRunStatus.COMPLETED
    assert sqlite.run.status is EvaluationRunStatus.COMPLETED
    assert [(item.case_id, item.outcome, item.deterministic_pass) for item in memory.results] == [
        (item.case_id, item.outcome, item.deterministic_pass) for item in sqlite.results
    ]
    restarted = SqliteEvaluationRepository(database)
    assert restarted.get_run(sqlite.run.run_id) == sqlite.run
    assert SqliteEvalManifestRepository(database).get_manifest(sqlite.run.run_id) == sqlite.manifest


def test_target_aware_runtime_offloads_durable_suite_lookup(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        runner = EvaluationRunner(
            repository=repository,
            executor=_StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        service = TargetAwareEvaluationService(
            repository=repository,
            runner=runner,
            suites=(),
            target_enricher=cast(Any, _IdentityTargetEnricher()),
        )
        assets = _SlowSuiteRepository(tmp_path / "evaluation.sqlite3")
        assets.create_suite(_suite())
        service.attach_suite_assets(assets)
        assets.delay_connections = True

        pending = asyncio.create_task(
            service.run_suite(
                suite_ref=suite_ref(_suite()),
                snapshot=_snapshot(),
            )
        )
        await asyncio.sleep(0.01)

        assert not pending.done()
        summary = await pending
        assert summary.run.status is EvaluationRunStatus.COMPLETED

    asyncio.run(scenario())


def test_control_plane_evaluation_reads_use_awaitable_service_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RecordingHistoryRepository(tmp_path / "evaluation.sqlite3", delay=0.06)
        repository.delay_connections = False
        run = _run(status=EvaluationRunStatus.COMPLETED)
        repository.save_run(run)
        repository.delay_connections = True
        runner = EvaluationRunner(
            repository=repository,
            executor=_StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        service = EvaluationService(
            repository=repository,
            runner=runner,
            suites=(_suite(),),
        )
        resources = EvaluationRunResourceService(service)

        pending = asyncio.create_task(resources.list_resources(cast(Any, None), cast(Any, None)))
        await asyncio.sleep(0.01)

        assert not pending.done()
        listed = await pending
        assert listed[0]["id"] == run.run_id

    asyncio.run(scenario())


def test_portability_evaluation_mutation_uses_async_runtime_methods() -> None:
    async def scenario() -> None:
        service = _AsyncOnlyEvaluationService()
        handler = EvaluationSuiteImportMutationHandler(cast(EvaluationService, service))
        suite = _suite()

        await handler.preflight(cast(Any, object()), suite, cast(Any, object()))
        token = await handler.apply(cast(Any, object()), suite, cast(Any, object()))
        assert token == EvaluationSuiteImportToken(
            suite_ref=suite_ref(suite),
            checksum="sha256:test",
        )
        await handler.rollback(
            cast(Any, object()),
            suite,
            token,
            cast(Any, object()),
        )
        assert service.calls == ["get", "create", "delete"]

    asyncio.run(scenario())
