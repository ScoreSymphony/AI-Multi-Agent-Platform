from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from dataclasses import replace
from typing import cast

import pytest

from ai_multi_agent_platform.coordination import (
    AsyncCoordinatorRepositoryAdapter,
    CoordinationPhase,
    InMemoryCoordinatorRepository,
    StepCoordinationRecord,
)
from ai_multi_agent_platform.coordination.plan_step_coordinator import (
    CanonicalRunKernel,
    DurablePlanStepCoordinator,
)
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

OWNER = OwnerRef(type="user", id="coordination-review-regression-user")


def _plan_fixture() -> tuple[Plan, Step, StepCoordinationRecord]:
    plan = Plan(task_id=new_id("task"), owner_ref=OWNER, active=True)
    step = Step(plan_id=plan.id, title="pending", owner_ref=OWNER)
    record = StepCoordinationRecord(
        task_id=plan.task_id,
        plan_id=plan.id,
        plan_revision=plan.revision,
        step_id=step.id,
        phase=CoordinationPhase.BLOCKED,
    )
    return plan, step, record


async def _wait_for_event(event: threading.Event, *, timeout: float = 1.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    return event.is_set()


class _OpaqueRepository:
    """Protocol-compatible repository that is unhashable and cannot be weak-referenced."""

    __slots__ = ("_inner",)
    __hash__ = None

    def __init__(self) -> None:
        self._inner = InMemoryCoordinatorRepository()

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


class _BlockingSnapshotRepository(InMemoryCoordinatorRepository):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot_started = threading.Event()
        self.release_snapshot = threading.Event()
        self.block_snapshot = False

    def get_plan(self, plan_id: str):  # type: ignore[no-untyped-def]
        state = super().get_plan(plan_id)
        if self.block_snapshot:
            self.snapshot_started.set()
            self.release_snapshot.wait(timeout=5)
        return state


class _SnapshotOnlyAdapter(AsyncCoordinatorRepositoryAdapter):
    async def get_plan(self, plan_id: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"split get_plan read used for {plan_id}")

    async def list_step_records(self, plan_id: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"split list_step_records read used for {plan_id}")


class _RegistryOwner:
    pass


class _OffloadToken:
    pass


def test_unhashable_nonweakrefable_repository_can_share_one_offload() -> None:
    repository = _OpaqueRepository()
    with pytest.raises(TypeError):
        weakref.ref(repository)

    canonical = cast(CoordinatorRepository, repository)
    first = AsyncCoordinatorRepositoryAdapter(canonical)
    second = AsyncCoordinatorRepositoryAdapter(canonical)

    assert first.offload is second.offload

    plan, step, record = _plan_fixture()

    async def scenario() -> None:
        created = await first.create_plan(plan, (step,), (record,))
        assert created.plan == plan
        assert (await second.get_plan(plan.id)).plan == plan

    asyncio.run(scenario())


def test_shared_registry_releases_offload_after_final_owner_only() -> None:
    repository = object()
    registry = SharedPersistenceOffloadRegistry[_OffloadToken]()
    first_owner = _RegistryOwner()
    first_offload = registry.resolve(
        repository,
        owner=first_owner,
        requested=None,
        factory=_OffloadToken,
    )
    offload_reference = weakref.ref(first_offload)

    second_owner = _RegistryOwner()
    second_offload = registry.resolve(
        repository,
        owner=second_owner,
        requested=None,
        factory=_OffloadToken,
    )
    assert second_offload is first_offload

    del first_owner
    gc.collect()
    assert offload_reference() is first_offload

    del first_offload
    del second_offload
    gc.collect()
    assert offload_reference() is not None

    del second_owner
    gc.collect()
    assert offload_reference() is None


def test_owner_release_callback_can_reenter_registry_during_resolve() -> None:
    registry = SharedPersistenceOffloadRegistry[_OffloadToken]()
    first_repository = object()
    owner_holder = [_RegistryOwner()]
    registry.resolve(
        first_repository,
        owner=owner_holder[0],
        requested=None,
        factory=_OffloadToken,
    )

    second_repository = object()
    second_owner = _RegistryOwner()
    completed = threading.Event()
    failures: list[BaseException] = []

    def factory() -> _OffloadToken:
        owner_holder.clear()
        gc.collect()
        return _OffloadToken()

    def resolve_second_repository() -> None:
        try:
            registry.resolve(
                second_repository,
                owner=second_owner,
                requested=None,
                factory=factory,
            )
        except BaseException as exc:  # pragma: no cover - asserted after thread completion
            failures.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=resolve_second_repository, daemon=True)
    worker.start()
    worker.join(timeout=1)

    assert completed.is_set(), "weakref callback deadlocked while resolve held the registry lock"
    assert failures == []


def test_plan_snapshot_holds_serialization_boundary_across_all_reads() -> None:
    async def scenario() -> None:
        repository = _BlockingSnapshotRepository()
        plan, step, record = _plan_fixture()
        repository.create_plan(plan, (step,), (record,))
        repository.block_snapshot = True
        adapter = AsyncCoordinatorRepositoryAdapter(repository)

        snapshot = asyncio.create_task(adapter.get_plan_snapshot(plan.id))
        assert await _wait_for_event(repository.snapshot_started)

        updated = replace(record, phase=CoordinationPhase.READY)
        write = asyncio.create_task(
            adapter.save_step(
                step=step,
                record=updated,
                expected_revision=record.revision,
            )
        )
        await asyncio.sleep(0.02)
        assert not write.done()

        repository.release_snapshot.set()
        state, records = await snapshot
        assert records == (record,)

        saved = await write
        assert saved.phase is CoordinationPhase.READY
        assert (await adapter.get_plan(plan.id)).store_revision == state.store_revision + 1

    asyncio.run(scenario())


def test_runtime_projection_uses_atomic_snapshot_api() -> None:
    repository = InMemoryCoordinatorRepository()
    plan, step, record = _plan_fixture()
    repository.create_plan(plan, (step,), (record,))
    runtime_repository = _SnapshotOnlyAdapter(repository)
    coordinator = DurablePlanStepCoordinator(
        repository=repository,
        kernel=cast(CanonicalRunKernel, object()),
        coordinator_id="coordination-review-regression",
        runtime_repository=runtime_repository,
    )

    projection = asyncio.run(coordinator.async_projection(plan.id))

    assert projection.plan_id == plan.id
    assert len(projection.steps) == 1
    assert projection.steps[0].step_id == step.id
    assert projection.steps[0].phase is CoordinationPhase.BLOCKED
