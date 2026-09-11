"""Distributed application-build lifecycle over canonical #14/#433 Worker primitives.

This module owns only application-specific translation and result admission. Scheduling,
Worker ownership, remote Workspace transfer, transport, reconciliation and Artifact publication
remain owned by the existing distributed runtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionSnapshot,
    HealthStatus,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts import ExecutionRequest as KernelExecutionRequest
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileRecord
from ai_multi_agent_platform.distributed.models import WorkerJobRequest, WorkerJobResult
from ai_multi_agent_platform.distributed.registry import RegistryError
from ai_multi_agent_platform.distributed.runtime import (
    DispatchAuthorizationError,
    DispatchRecord,
    DispatchState,
    DistributedRuntime,
)
from ai_multi_agent_platform.distributed.scheduler import NoEligibleWorkerError
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionRequest,
    ExecutionResult,
    Executor,
)
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository

from .contracts import ApplicationReleaseRepository
from .execution import APPLICATION_BUILD_ACTION
from .models import ApplicationRelease, BuildTargetState
from .placement import job_requirements_for_target

APPLICATION_BUILD_WORKER_INPUT_KEY = "application_build_worker"
APPLICATION_BUILD_WORKER_SCHEMA = "ai-multi-agent-platform/application-build-worker/v1"
_METADATA_NAMESPACE = "distributed-application-build"


class DistributedApplicationBuildLifecycleBackend(LifecycleBackend):
    """Route one canonical application build Run through the canonical distributed runtime."""

    def __init__(
        self,
        releases: ApplicationReleaseRepository,
        files: FileProvider,
        bindings: RunWorkspaceBindingRepository,
        runtime: DistributedRuntime,
    ) -> None:
        self._releases = releases
        self._files = files
        self._bindings = bindings
        self.runtime = runtime

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="distributed-application-build-lifecycle",
            provider_type="execution",
            supported_operations=("start", "get", "cancel"),
            health=HealthStatus.HEALTHY,
            available=True,
            resources={"scheduler": "distributed-runtime", "workspace": "remote-materialization"},
        )

    async def start(self, request: KernelExecutionRequest) -> ExecutionHandle:
        release, target = await self._release_target_for_run(request.run_id)
        self._validate_request(request, release, target)
        binding = await self._bindings.get(request.run_id)
        if binding is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed application build Run has no immutable Workspace binding",
            )
        if (
            binding.task_id != request.subject_id
            or binding.workspace_id != release.workspace_id
            or binding.workspace_snapshot_id != release.workspace_snapshot_id
            or binding.content_checksum != release.workspace_content_checksum
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed application build binding differs from release provenance",
            )
        if release.build_specification.secret_environment:
            # #748 owns the final reference->ephemeral Worker environment delivery seam. Never
            # downgrade to plaintext or silently execute without declared secret-backed inputs.
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                (
                    "remote application build secret_environment requires scoped Worker "
                    "secret delivery"
                ),
                provider_id=self.descriptor.provider_id,
            )

        timeout_seconds = _timeout_seconds(release, request.context)
        execution_context = replace(
            request.context,
            control=replace(
                request.context.control,
                timeout_seconds=timeout_seconds,
            ),
        )
        execution = replace(
            request,
            context=execution_context,
            input={
                **request.input,
                APPLICATION_BUILD_WORKER_INPUT_KEY: _build_worker_payload(release, target),
            },
        )
        default_idempotency_key = (
            f"application-build:{release.release_id}:{target.target.target_id}:"
            f"{request.run_id}"
        )
        job = WorkerJobRequest(
            worker_job_id=_worker_job_id(request.run_id),
            execution=execution,
            requirements=job_requirements_for_target(
                release.build_specification,
                target.target,
            ),
            workspace_ref=binding.workspace_id,
            snapshot_ref=binding.workspace_snapshot_id,
            secret_refs=release.build_specification.secret_references,
            actor_ref=_actor_ref(request.context),
            timeout_seconds=timeout_seconds,
            idempotency_key=request.context.control.idempotency_key or default_idempotency_key,
        )
        try:
            record = await self.runtime.dispatch(job)
        except NoEligibleWorkerError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                str(exc),
                retryable=True,
                provider_id=self.descriptor.provider_id,
            ) from exc
        except DispatchAuthorizationError as exc:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                str(exc),
                provider_id=self.descriptor.provider_id,
            ) from exc
        except ContractError:
            raise
        except RegistryError as exc:
            raise _registry_error(exc, provider_id=self.descriptor.provider_id) from exc

        if record.handle is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed application build dispatch returned no execution handle",
                provider_id=self.descriptor.provider_id,
            )
        return _handle(record, release, target, self._node_id(record))

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        worker_job_id = _worker_job_id(run_id)
        try:
            await self.runtime.reconcile()
            record = self.runtime.get_record(worker_job_id)
        except RegistryError as exc:
            raise _registry_error(
                exc,
                provider_id=self.descriptor.provider_id,
                unknown_is_not_found=True,
            ) from exc

        if record.state in {DispatchState.LOST, DispatchState.CANCEL_PENDING}:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"distributed application build is not currently reachable: {run_id}",
                retryable=True,
                provider_id=self.descriptor.provider_id,
            )
        if record.snapshot is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"distributed application build has no observable snapshot: {run_id}",
                provider_id=self.descriptor.provider_id,
            )

        release, target = await self._release_target_for_run(run_id)
        node_id = self._node_id(record)
        snapshot = _snapshot(record, record.snapshot, release, target, node_id)
        if snapshot.status.value != "succeeded":
            return snapshot
        try:
            result = await self.runtime.result(worker_job_id)
        except RegistryError as exc:
            raise _registry_error(exc, provider_id=self.descriptor.provider_id) from exc
        if result is None or result.execution is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "successful distributed application build has no terminal Worker result",
                provider_id=self.descriptor.provider_id,
            )
        try:
            return await self._admit_remote_result(
                record,
                result,
                result.execution,
                release,
                target,
                context,
                node_id=node_id,
            )
        except ContractError as exc:
            # A Worker success without exact canonical File/Artifact evidence is a failed build,
            # not a successful Run whose release admission merely happened to fail later.
            return _failed_evidence_snapshot(
                record,
                result.execution,
                release,
                target,
                exc.message,
                node_id=node_id,
            )

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        worker_job_id = _worker_job_id(run_id)
        try:
            record = await self.runtime.cancel(worker_job_id)
        except RegistryError as exc:
            raise _registry_error(
                exc,
                provider_id=self.descriptor.provider_id,
                unknown_is_not_found=True,
            ) from exc
        if record.state is DispatchState.CANCEL_PENDING or record.snapshot is None:
            message = (
                "distributed application build cancellation is pending Worker reconciliation: "
                f"{run_id}"
            )
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                message,
                retryable=True,
                provider_id=self.descriptor.provider_id,
            )
        release, target = await self._release_target_for_run(run_id)
        return _snapshot(
            record,
            record.snapshot,
            release,
            target,
            self._node_id(record),
        )

    async def _release_target_for_run(
        self,
        run_id: str,
    ) -> tuple[ApplicationRelease, BuildTargetState]:
        release = await self._releases.find_run(run_id)
        if release is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application release for distributed build Run not found: {run_id}",
            )
        matches = [target for target in release.targets if target.run_id == run_id]
        if len(matches) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed application build Run must belong to exactly one release target",
            )
        return release, matches[0]

    async def _admit_remote_result(
        self,
        record: DispatchRecord,
        result: WorkerJobResult,
        snapshot: ExecutionSnapshot,
        release: ApplicationRelease,
        target: BuildTargetState,
        operation: OperationContext,
        *,
        node_id: str | None,
    ) -> ExecutionSnapshot:
        _validate_worker_result_identity(snapshot, release, target)
        context = _data_context(operation, target, release)
        files = await self._files.list_files(context)
        artifact_ref, file_record = _select_output_file(
            files,
            result.artifact_refs,
            release,
            target,
        )
        if not await self._files.verify_checksum(file_record.file_id, context):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "remote application build output failed canonical File checksum verification",
            )
        output = dict(snapshot.output)
        executor_output = output.get("output")
        if not isinstance(executor_output, Mapping):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "remote application build Worker result has no executor output object",
            )
        nested: dict[str, JsonValue] = dict(executor_output)
        nested["application_build"] = {
            "release_id": release.release_id,
            "target_id": target.target.target_id,
            "artifact_id": artifact_ref,
            "file_id": file_record.file_id,
            "filename": PurePosixPath(target.target.output_path).name,
            "media_type": file_record.content_type or "application/octet-stream",
            "sha256": file_record.sha256,
        }
        output["output"] = nested
        return replace(
            snapshot,
            output=output,
            adapter_metadata=(
                *snapshot.adapter_metadata,
                _metadata(record, release, target, node_id),
            ),
        )

    @staticmethod
    def _validate_request(
        request: KernelExecutionRequest,
        release: ApplicationRelease,
        target: BuildTargetState,
    ) -> None:
        if request.subject_type != "task" or request.subject_id != target.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed application build requires the target's canonical Task Run",
            )
        if request.context.project_id != release.project_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed application build execution context is outside the release project",
            )

    def _node_id(self, record: DispatchRecord) -> str | None:
        try:
            return self.runtime.registry.get_worker(record.worker_id).node_id
        except RegistryError:
            return None


class ApplicationBuildWorkerLifecycleBackend(LifecycleBackend):
    """Worker-local application command route for an already-materialized Workspace.

    The Control Plane sends only provider-neutral build metadata and safe non-secret environment
    values. Host filesystem paths are derived locally from #433 materialization state.
    """

    def __init__(
        self,
        executor: Executor,
        *,
        workspace: str,
        workspace_id: str,
        snapshot_id: str,
        fallback: LifecycleBackend | None = None,
    ) -> None:
        for name, value in (
            ("workspace", workspace),
            ("workspace_id", workspace_id),
            ("snapshot_id", snapshot_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        self._executor = executor
        self._workspace = workspace
        self._workspace_id = workspace_id
        self._snapshot_id = snapshot_id
        self._fallback = fallback
        self._results: dict[str, ExecutionResult] = {}
        self._fallback_runs: set[str] = set()
        self._cancellations: dict[str, CancellationToken] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=f"application-build-worker:{self._executor.descriptor.executor_id}",
            provider_type="execution",
            supported_operations=("start", "get", "cancel"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def start(self, request: KernelExecutionRequest) -> ExecutionHandle:
        payload = _worker_payload(request.input)
        if payload is None:
            if self._fallback is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Worker lifecycle received a non-application build request",
                )
            self._fallback_runs.add(request.run_id)
            return await self._fallback.start(request)

        _validate_materialized_worker_binding(
            payload,
            workspace_id=self._workspace_id,
            snapshot_id=self._snapshot_id,
        )
        if request.run_id not in self._results and request.run_id not in self._tasks:
            cancellation = CancellationToken()
            self._cancellations[request.run_id] = cancellation
            task = asyncio.create_task(self._execute_build(request, payload, cancellation))
            self._tasks[request.run_id] = task
        return ExecutionHandle(
            run_id=request.run_id,
            backend_ref=f"application-build-worker:{request.run_id}",
        )

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        if run_id in self._fallback_runs:
            assert self._fallback is not None
            return await self._fallback.get(run_id, context)
        result = self._results.get(run_id)
        if result is not None:
            return _execution_result_snapshot(result)
        task = self._tasks.get(run_id)
        if task is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application build Worker execution not found: {run_id}",
            )
        if task.done():
            await task
            result = self._results.get(run_id)
            if result is None:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    f"application build Worker produced no result: {run_id}",
                )
            return _execution_result_snapshot(result)
        return ExecutionSnapshot(
            run_id=run_id,
            status=RunStatus.RUNNING,
            output={"state": "active-remote-application-build"},
        )

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        if run_id in self._fallback_runs:
            assert self._fallback is not None
            return await self._fallback.cancel(run_id, context)
        result = self._results.get(run_id)
        if result is not None:
            return _execution_result_snapshot(result)
        task = self._tasks.get(run_id)
        cancellation = self._cancellations.get(run_id)
        if task is None or cancellation is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application build Worker execution not found: {run_id}",
            )
        await self._executor.cancel(cancellation)
        await task
        result = self._results.get(run_id)
        if result is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"cancelled application build Worker produced no result: {run_id}",
            )
        return _execution_result_snapshot(result)

    async def _execute_build(
        self,
        request: KernelExecutionRequest,
        payload: Mapping[str, object],
        cancellation: CancellationToken,
    ) -> None:
        try:
            command = _string_array(payload, "command")
            source_path = _optional_string_field(payload, "source_path")
            output_path = _required_string_field(payload, "output_path")
            environment = _string_mapping(payload, "environment")
            result = await self._executor.execute(
                ExecutionRequest(
                    task_id=(
                        request.subject_id
                        if request.subject_type == "task"
                        else request.context.correlation_id
                    ),
                    run_id=request.run_id,
                    step_id=request.subject_id if request.subject_type == "step" else None,
                    correlation_id=request.context.correlation_id,
                    action=APPLICATION_BUILD_ACTION,
                    workspace=self._workspace,
                    arguments={
                        "command": command,
                        "source_path": source_path,
                        "output_path": output_path,
                    },
                    environment=environment,
                    timeout_seconds=request.context.control.timeout_seconds,
                    cancellation=cancellation,
                )
            )
            self._results[request.run_id] = replace(
                result,
                output={
                    **result.output,
                    "application_build_request": _worker_result_identity(payload),
                },
            )
        finally:
            self._cancellations.pop(request.run_id, None)
            self._tasks.pop(request.run_id, None)


def application_build_worker_input(
    release: ApplicationRelease,
    target: BuildTargetState,
) -> dict[str, JsonValue]:
    """Public constructor for portable remote build metadata used by tests/adapters."""

    return {APPLICATION_BUILD_WORKER_INPUT_KEY: _build_worker_payload(release, target)}


def _build_worker_payload(
    release: ApplicationRelease,
    target: BuildTargetState,
) -> dict[str, JsonValue]:
    specification = release.build_specification
    return {
        "schema": APPLICATION_BUILD_WORKER_SCHEMA,
        "release_id": release.release_id,
        "target_id": target.target.target_id,
        "build_specification_id": specification.spec_id,
        "build_specification_revision": specification.revision,
        "source_revision": release.source_revision,
        "workspace_id": release.workspace_id,
        "workspace_snapshot_id": release.workspace_snapshot_id,
        "workspace_content_checksum": release.workspace_content_checksum,
        "command": list(specification.command),
        "source_path": specification.source_path,
        "output_path": target.target.output_path,
        "environment": dict(specification.environment),
    }


def _worker_payload(input_data: Mapping[str, JsonValue]) -> Mapping[str, object] | None:
    raw = input_data.get(APPLICATION_BUILD_WORKER_INPUT_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "application build Worker input must be an object",
        )
    if raw.get("schema") != APPLICATION_BUILD_WORKER_SCHEMA:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "unsupported application build Worker input schema",
        )
    return raw


def _worker_result_identity(payload: Mapping[str, object]) -> dict[str, JsonValue]:
    return {
        key: _required_identity_value(payload, key)
        for key in (
            "release_id",
            "target_id",
            "build_specification_id",
            "build_specification_revision",
            "source_revision",
            "workspace_id",
            "workspace_snapshot_id",
            "workspace_content_checksum",
            "output_path",
        )
    }


def _required_identity_value(payload: Mapping[str, object], key: str) -> JsonValue:
    value = payload.get(key)
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    raise ContractError(
        ErrorCode.CONTRACT_VIOLATION,
        f"application build Worker input has invalid {key}",
    )


def _validate_materialized_worker_binding(
    payload: Mapping[str, object],
    *,
    workspace_id: str,
    snapshot_id: str,
) -> None:
    if payload.get("workspace_id") != workspace_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "application build Worker payload references another Workspace",
        )
    if payload.get("workspace_snapshot_id") != snapshot_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "application build Worker payload references another Workspace snapshot",
        )


def _validate_worker_result_identity(
    snapshot: ExecutionSnapshot,
    release: ApplicationRelease,
    target: BuildTargetState,
) -> None:
    outer = snapshot.output.get("output")
    if not isinstance(outer, Mapping):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "remote application build result has no executor output",
        )
    raw = outer.get("application_build_request")
    if not isinstance(raw, Mapping):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "remote application build result has no request provenance",
        )
    expected: dict[str, JsonValue] = {
        "release_id": release.release_id,
        "target_id": target.target.target_id,
        "build_specification_id": release.build_specification.spec_id,
        "build_specification_revision": release.build_specification.revision,
        "source_revision": release.source_revision,
        "workspace_id": release.workspace_id,
        "workspace_snapshot_id": release.workspace_snapshot_id,
        "workspace_content_checksum": release.workspace_content_checksum,
        "output_path": target.target.output_path,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "remote application build result provenance differs from requested build",
        )


def _select_output_file(
    files: tuple[FileRecord, ...],
    artifact_refs: tuple[str, ...],
    release: ApplicationRelease,
    target: BuildTargetState,
) -> tuple[str, FileRecord]:
    artifact_set = set(artifact_refs)
    candidates: list[tuple[str, FileRecord]] = []
    for record in files:
        metadata = record.metadata
        if metadata.get("workspace_id") != release.workspace_id:
            continue
        if metadata.get("workspace_snapshot_id") != release.workspace_snapshot_id:
            continue
        if metadata.get("relative_path") != target.target.output_path:
            continue
        matching_artifacts = sorted(artifact_set.intersection(record.artifact_ids))
        for artifact_id in matching_artifacts:
            candidates.append((artifact_id, record))
    if len(candidates) != 1:
        message = (
            "remote application build output was not returned as one canonical changed "
            "File/Artifact"
        )
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, message)
    return candidates[0]


def _execution_result_snapshot(result: ExecutionResult) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        run_id=result.run_id,
        status=result.status,
        output={
            "result_code": result.result_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "output": result.output,
            "artifacts": [artifact.relative_path for artifact in result.artifacts],
        },
        adapter_metadata=tuple(
            AdapterMetadata(namespace=namespace, values=dict(values))
            for namespace, values in sorted(result.adapter_metadata.items())
        ),
    )


def _worker_job_id(run_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:distributed-application-build:{run_id}",
    )
    return f"worker_job_{value}"


def _metadata(
    record: DispatchRecord,
    release: ApplicationRelease,
    target: BuildTargetState,
    node_id: str | None,
) -> AdapterMetadata:
    return AdapterMetadata(
        namespace=_METADATA_NAMESPACE,
        values={
            "worker_job_id": record.job.worker_job_id,
            "worker_id": record.worker_id,
            "node_id": node_id,
            "dispatch_state": record.state.value,
            "release_id": release.release_id,
            "target_id": target.target.target_id,
        },
    )


def _handle(
    record: DispatchRecord,
    release: ApplicationRelease,
    target: BuildTargetState,
    node_id: str | None,
) -> ExecutionHandle:
    assert record.handle is not None
    return replace(
        record.handle,
        adapter_metadata=(
            *record.handle.adapter_metadata,
            _metadata(record, release, target, node_id),
        ),
    )


def _snapshot(
    record: DispatchRecord,
    snapshot: ExecutionSnapshot,
    release: ApplicationRelease,
    target: BuildTargetState,
    node_id: str | None,
) -> ExecutionSnapshot:
    return replace(
        snapshot,
        adapter_metadata=(
            *snapshot.adapter_metadata,
            _metadata(record, release, target, node_id),
        ),
    )


def _failed_evidence_snapshot(
    record: DispatchRecord,
    snapshot: ExecutionSnapshot,
    release: ApplicationRelease,
    target: BuildTargetState,
    message: str,
    *,
    node_id: str | None,
) -> ExecutionSnapshot:
    output = dict(snapshot.output)
    output["stderr"] = message
    return replace(
        snapshot,
        status=RunStatus.FAILED,
        output=output,
        adapter_metadata=(
            *snapshot.adapter_metadata,
            _metadata(record, release, target, node_id),
        ),
    )


def _data_context(
    operation: OperationContext,
    target: BuildTargetState,
    release: ApplicationRelease,
) -> DataAccessContext:
    if target.task_id is None or target.run_id is None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "remote application build target is missing canonical Task/Run identity",
        )
    return DataAccessContext(
        operation=operation,
        actor_ref=_actor_ref(operation),
        task_id=target.task_id,
        run_id=target.run_id,
        audit_metadata={
            "source": "distributed-application-build",
            "release_id": release.release_id,
            "target_id": target.target.target_id,
        },
    )


def _actor_ref(operation: OperationContext) -> str:
    if operation.owner_type is not None and operation.owner_id is not None:
        return f"{operation.owner_type}:{operation.owner_id}"
    return "service:application-distribution"


def _timeout_seconds(
    release: ApplicationRelease,
    context: OperationContext,
) -> float | None:
    configured = release.build_specification.resource_hints.get("timeout_seconds")
    if configured is None:
        return context.control.timeout_seconds
    if isinstance(configured, bool) or not isinstance(configured, int | float) or configured <= 0:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "build resource_hints.timeout_seconds must be a positive number",
        )
    return float(configured)


def _required_string_field(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"application build Worker input is missing {key}",
        )
    return value


def _optional_string_field(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"application build Worker input has invalid {key}",
        )
    return value


def _string_array(payload: Mapping[str, object], key: str) -> list[JsonValue]:
    raw = payload.get(key)
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(item, str) or not item.strip() for item in raw)
    ):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"application build Worker input has invalid {key}",
        )
    return list(raw)


def _string_mapping(payload: Mapping[str, object], key: str) -> dict[str, str]:
    raw = payload.get(key)
    if not isinstance(raw, Mapping):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"application build Worker input has invalid {key}",
        )
    result: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"application build Worker input has invalid {key}",
            )
        result[name] = value
    return result


def _registry_error(
    exc: RegistryError,
    *,
    provider_id: str,
    unknown_is_not_found: bool = False,
) -> ContractError:
    message = str(exc)
    if unknown_is_not_found and "unknown dispatched worker job" in message:
        return ContractError(ErrorCode.NOT_FOUND, message, provider_id=provider_id)
    if (
        "no attached dispatcher" in message
        or "not currently reachable" in message
        or "worker result is not currently reachable" in message
    ):
        return ContractError(
            ErrorCode.UNAVAILABLE,
            message,
            retryable=True,
            provider_id=provider_id,
        )
    return ContractError(ErrorCode.CONFLICT, message, provider_id=provider_id)


__all__ = [
    "APPLICATION_BUILD_WORKER_INPUT_KEY",
    "APPLICATION_BUILD_WORKER_SCHEMA",
    "ApplicationBuildWorkerLifecycleBackend",
    "DistributedApplicationBuildLifecycleBackend",
    "application_build_worker_input",
]
