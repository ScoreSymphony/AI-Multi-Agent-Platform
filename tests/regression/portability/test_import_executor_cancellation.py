from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.portability import (
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreview,
    ImportPreviewService,
    PackageProvenance,
    PortablePackage,
    PortableResource,
    ResourceExport,
    ResourceSerializerRegistry,
    build_package,
    resource_dependency,
)
from ai_multi_agent_platform.portability.registry import ImportContext

_FIRST_TYPE = "regression.rollback-first"
_SECOND_TYPE = "regression.rollback-second"
_FIRST_ID = "first-1"
_SECOND_ID = "second-1"


class _Codec:
    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        *,
        depends_on_first: bool = False,
    ) -> None:
        self.resource_type = resource_type
        self._resource_id = resource_id
        self._depends_on_first = depends_on_first

    def serialize(self, value: object) -> ResourceExport:
        dependencies = ()
        if self._depends_on_first:
            dependencies = (
                resource_dependency(
                    _FIRST_TYPE,
                    _FIRST_ID,
                    purpose="force deterministic regression-test import ordering",
                ),
            )
        return ResourceExport(
            resource_id=self._resource_id,
            resource_version="1",
            payload={"value": str(value)},
            dependencies=dependencies,
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        del context
        return resource.payload["value"]


class _AppliedHandler:
    resource_type = _FIRST_TYPE

    def __init__(
        self,
        state: list[str],
        *,
        rollback_started: asyncio.Event | None = None,
        rollback_release: asyncio.Event | None = None,
        rollback_error: BaseException | None = None,
    ) -> None:
        self._state = state
        self._rollback_started = rollback_started
        self._rollback_release = rollback_release
        self._rollback_error = rollback_error
        self.rollback_calls = 0

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        rendered = str(value)
        self._state.append(rendered)
        return rendered

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        self.rollback_calls += 1
        if self._rollback_started is not None:
            self._rollback_started.set()
        if self._rollback_release is not None:
            await self._rollback_release.wait()
        if self._rollback_error is not None:
            raise self._rollback_error
        self._state.remove(str(token))


class _SecondHandler:
    resource_type = _SECOND_TYPE

    def __init__(
        self,
        *,
        apply_operation: Callable[[], Awaitable[object]],
    ) -> None:
        self._apply_operation = apply_operation

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, value, context
        return await self._apply_operation()

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, token, context


def _portable_fixture() -> tuple[ResourceSerializerRegistry, PortablePackage, ImportPreview]:
    serializers = ResourceSerializerRegistry()
    serializers.register(_Codec(_FIRST_TYPE, _FIRST_ID))
    serializers.register(_Codec(_SECOND_TYPE, _SECOND_ID, depends_on_first=True))
    first = serializers.serialize(_FIRST_TYPE, "first")
    second = serializers.serialize(_SECOND_TYPE, "second")
    package = build_package(
        source_platform_version="0.0.1",
        resources=(second, first),
        provenance=PackageProvenance(source="portability-cancellation-regression"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    assert preview.import_order == ((_FIRST_TYPE, _FIRST_ID), (_SECOND_TYPE, _SECOND_ID))
    return serializers, package, preview


def test_cancelled_import_rolls_back_prior_mutations_under_repeated_cancellation() -> None:
    async def scenario() -> None:
        serializers, package, preview = _portable_fixture()
        state: list[str] = []
        apply_started = asyncio.Event()
        block_apply = asyncio.Event()
        rollback_started = asyncio.Event()
        rollback_release = asyncio.Event()
        first = _AppliedHandler(
            state,
            rollback_started=rollback_started,
            rollback_release=rollback_release,
        )

        async def block_second_apply() -> object:
            apply_started.set()
            await block_apply.wait()
            return "unused"

        mutations = ImportMutationRegistry()
        mutations.register(first)
        mutations.register(_SecondHandler(apply_operation=block_second_apply))
        task = asyncio.create_task(ImportExecutor(serializers, mutations).execute(package, preview))

        await apply_started.wait()
        task.cancel()
        await rollback_started.wait()
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        rollback_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert state == []
        assert first.rollback_calls == 1

    asyncio.run(scenario())


def test_cancelled_import_preserves_cancellation_when_rollback_fails() -> None:
    async def scenario() -> None:
        serializers, package, preview = _portable_fixture()
        state: list[str] = []
        apply_started = asyncio.Event()
        block_apply = asyncio.Event()
        secret = "synthetic-secret-rollback-value"
        first = _AppliedHandler(state, rollback_error=RuntimeError(secret))

        async def block_second_apply() -> object:
            apply_started.set()
            await block_apply.wait()
            return "unused"

        mutations = ImportMutationRegistry()
        mutations.register(first)
        mutations.register(_SecondHandler(apply_operation=block_second_apply))
        task = asyncio.create_task(ImportExecutor(serializers, mutations).execute(package, preview))

        await apply_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task

        notes = "\n".join(getattr(cancelled.value, "__notes__", ()))
        assert "rollback was incomplete" in notes
        assert "1 rollback operation(s) failed" in notes
        assert secret not in notes
        assert state == ["first"]
        assert first.rollback_calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
def test_process_signal_is_preserved_after_prior_mutations_are_rolled_back(
    signal_type: type[BaseException],
) -> None:
    async def scenario() -> None:
        serializers, package, preview = _portable_fixture()
        state: list[str] = []
        first = _AppliedHandler(state)
        signal = signal_type("process-control")

        async def raise_process_signal() -> object:
            raise signal

        mutations = ImportMutationRegistry()
        mutations.register(first)
        mutations.register(_SecondHandler(apply_operation=raise_process_signal))

        with pytest.raises(signal_type) as caught:
            await ImportExecutor(serializers, mutations).execute(package, preview)

        assert caught.value is signal
        assert state == []
        assert first.rollback_calls == 1

    asyncio.run(scenario())


def test_primary_import_failure_wins_cancellation_while_rollback_settles() -> None:
    async def scenario() -> None:
        serializers, package, preview = _portable_fixture()
        state: list[str] = []
        rollback_started = asyncio.Event()
        rollback_release = asyncio.Event()
        first = _AppliedHandler(
            state,
            rollback_started=rollback_started,
            rollback_release=rollback_release,
        )
        primary = ContractError(ErrorCode.BACKEND_ERROR, "primary import failure")

        async def fail_second_apply() -> object:
            raise primary

        mutations = ImportMutationRegistry()
        mutations.register(first)
        mutations.register(_SecondHandler(apply_operation=fail_second_apply))
        task = asyncio.create_task(ImportExecutor(serializers, mutations).execute(package, preview))

        await rollback_started.wait()
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        rollback_release.set()

        with pytest.raises(ContractError) as failed:
            await task

        assert failed.value.code is ErrorCode.BACKEND_ERROR
        assert failed.value.__cause__ is primary
        assert failed.value.details["rollback_complete"] is True
        assert state == []
        assert first.rollback_calls == 1

    asyncio.run(scenario())
