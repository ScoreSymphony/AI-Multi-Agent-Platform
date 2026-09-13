from __future__ import annotations

import ast
import asyncio
import re
import tomllib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.contracts import (
    ExecutionRequest,
    OperationContext,
    OperationControl,
    RetryMode,
)
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    FailoverFenceReceipt,
    JobRequirements,
    JsonDistributedStateStore,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Run, Task, new_id, validate_id
from ai_multi_agent_platform.testing import FakeLifecycleBackend

_CORE_ROOTS = (
    Path("src/ai_multi_agent_platform/contracts"),
    Path("src/ai_multi_agent_platform/domain"),
    Path("src/ai_multi_agent_platform/kernel"),
)
_NORTHBOUND_CLIENT_ROOTS = (
    Path("src/ai_multi_agent_platform/cli"),
    Path("src/ai_multi_agent_platform/conversations"),
    Path("src/ai_multi_agent_platform/terminal"),
)
_OPTIONAL_BACKEND_IMPORT_PREFIXES = (
    "ai_multi_agent_platform.adapters",
    "hermes",
    "forge",
    "litellm",
    "mcp",
)
_BACKEND_PRIVATE_TYPE_PREFIXES = ("hermes", "forge", "litellm", "mcp")
_MANDATORY_DEPENDENCY_DENYLIST = {
    "anthropic",
    "forge",
    "hermes",
    "litellm",
    "mcp",
    "openai",
}
_RUNTIME_BASE_TIME = datetime(2026, 9, 13, 19, 0, tzinfo=UTC)


class _ArchitectureFencer:
    async def fence(
        self,
        *,
        worker_id: str,
        job: WorkerJobRequest,
    ) -> FailoverFenceReceipt:
        return FailoverFenceReceipt(
            worker_job_id=job.worker_job_id,
            worker_id=worker_id,
            fence_ref=f"architecture:{worker_id}:{job.dispatch_attempt}",
            fenced_at=_RUNTIME_BASE_TIME + timedelta(seconds=2),
        )


def _canonical_python_files() -> Iterable[Path]:
    for root in _CORE_ROOTS:
        assert root.is_dir(), f"missing canonical source root: {root}"
        yield from sorted(root.rglob("*.py"))


def _northbound_client_python_files() -> Iterable[Path]:
    for root in _NORTHBOUND_CLIENT_ROOTS:
        assert root.is_dir(), f"missing northbound client source root: {root}"
        yield from sorted(root.rglob("*.py"))


def _current_package_parts(path: Path) -> list[str]:
    relative = path.relative_to(Path("src")).with_suffix("")
    parts = list(relative.parts)
    parts.pop()
    return parts


def _import_targets(path: Path, tree: ast.AST) -> set[str]:
    targets: set[str] = set()
    package_parts = _current_package_parts(path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            ascend = node.level - 1
            if ascend > len(package_parts):
                resolved_parts: list[str] = []
            elif ascend:
                resolved_parts = package_parts[:-ascend]
            else:
                resolved_parts = package_parts.copy()
            if node.module:
                resolved_parts.extend(node.module.split("."))
            module = ".".join(resolved_parts)
        else:
            module = node.module or ""

        if module:
            targets.add(module)
        for alias in node.names:
            if alias.name == "*":
                continue
            targets.add(f"{module}.{alias.name}" if module else alias.name)

    return targets


def _backend_import_targets(path: Path, tree: ast.AST) -> set[str]:
    return {
        target
        for target in _import_targets(path, tree)
        if any(
            target == prefix or target.startswith(f"{prefix}.")
            for prefix in _OPTIONAL_BACKEND_IMPORT_PREFIXES
        )
    }


def _public_type_nodes(tree: ast.AST) -> Iterable[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            yield node.annotation
        elif isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            yield node.returns
        elif isinstance(node, ast.ClassDef):
            yield from node.bases


def _expanded_type_nodes(type_node: ast.AST) -> Iterable[ast.AST]:
    pending = [type_node]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
            except SyntaxError:
                continue
            pending.append(parsed.body)
            continue
        pending.extend(ast.iter_child_nodes(node))


def _backend_private_type_names(tree: ast.AST) -> set[str]:
    offenders: set[str] = set()
    for type_node in _public_type_nodes(tree):
        for node in _expanded_type_nodes(type_node):
            name: str | None = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name is not None and name.lower().startswith(_BACKEND_PRIVATE_TYPE_PREFIXES):
                offenders.add(name)
    return offenders


def _runtime_node(name: str) -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name=name,
        resources=ResourceSnapshot(
            cpu_cores_total=4,
            cpu_cores_available=4,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        supported_runtimes=("python",),
    )


def _runtime_worker(node: NodeRecord) -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        concurrency_limit=1,
    )


def _runtime_job(
    preferred_worker_id: str,
    *,
    task_id: str,
    run_id: str,
) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=task_id,
                causation_id=run_id,
                owner_type="service",
                owner_id="service:issue-46-architecture",
                control=OperationControl(
                    idempotency_key=f"architecture:{run_id}",
                    retry_mode=RetryMode.IDEMPOTENT,
                ),
            ),
            input={"architecture_invariant": True},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            runtime="python",
            preferred_worker_ids=(preferred_worker_id,),
        ),
        idempotency_key=f"architecture-worker-job:{run_id}",
    )


def test_backend_private_type_guard_inspects_quoted_forward_references() -> None:
    tree = ast.parse(
        """
class LeakyContract:
    direct: "HermesResponse"
    nested: list["ForgeExecution"]

    def invoke(self, request: "LiteLLMRequest") -> "MCPResult": ...
"""
    )

    assert _backend_private_type_names(tree) == {
        "ForgeExecution",
        "HermesResponse",
        "LiteLLMRequest",
        "MCPResult",
    }


def test_backend_import_guard_resolves_relative_and_package_level_imports() -> None:
    path = Path("src/ai_multi_agent_platform/cli/example.py")
    tree = ast.parse(
        """
from ..adapters import forge
from ai_multi_agent_platform import adapters
import litellm.router
from mcp.server import Server
"""
    )

    offenders = _backend_import_targets(path, tree)
    assert "ai_multi_agent_platform.adapters" in offenders
    assert "ai_multi_agent_platform.adapters.forge" in offenders
    assert "litellm.router" in offenders
    assert "mcp.server" in offenders


def test_canonical_core_does_not_import_optional_backend_implementations() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _canonical_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        imports = sorted(_backend_import_targets(path, tree))
        if imports:
            offenders[path.as_posix()] = imports

    assert offenders == {}


def test_northbound_python_clients_do_not_import_optional_backend_implementations() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _northbound_client_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        imports = sorted(_backend_import_targets(path, tree))
        if imports:
            offenders[path.as_posix()] = imports

    assert offenders == {}


def test_canonical_core_public_types_do_not_reference_backend_private_classes() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _canonical_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        names = sorted(_backend_private_type_names(tree))
        if names:
            offenders[path.as_posix()] = names

    assert offenders == {}


def test_canonical_shaped_backend_ids_remain_namespaced_external_refs() -> None:
    owner = OwnerRef(type="user", id="issue-46")
    external_task_id = new_id("task")
    external_run_id = new_id("run")
    validate_id(external_task_id, "task")
    validate_id(external_run_id, "run")

    hermes_task = ExternalRef(system="hermes", kind="task", value=external_task_id)
    forge_run = ExternalRef(system="forge", kind="run", value=external_run_id)
    task = Task(title="Canonical identity", owner_ref=owner, external_refs=(hermes_task,))
    run = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=owner,
        correlation_id=task.id,
        external_refs=(forge_run,),
    )

    validate_id(task.id, "task")
    validate_id(run.id, "run")
    assert task.external_refs == (hermes_task,)
    assert run.external_refs == (forge_run,)
    assert task.id != external_task_id
    assert run.id != external_run_id


def test_distributed_restart_and_worker_failover_preserve_canonical_task_run_identity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "distributed.json"
        task_id = new_id("task")
        run_id = new_id("run")
        validate_id(task_id, "task")
        validate_id(run_id, "run")

        node_a = _runtime_node("architecture-a")
        node_b = _runtime_node("architecture-b")
        worker_a = _runtime_worker(node_a)
        worker_b = _runtime_worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
            ownership_fencer=_ArchitectureFencer(),
        )
        runtime.register(
            RegistrationRequest(node=node_a, workers=(worker_a,)),
            now=_RUNTIME_BASE_TIME,
        )
        runtime.register(
            RegistrationRequest(node=node_b, workers=(worker_b,)),
            now=_RUNTIME_BASE_TIME,
        )
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _runtime_job(
            worker_a.worker_id,
            task_id=task_id,
            run_id=run_id,
        )

        original = await runtime.dispatch(job, now=_RUNTIME_BASE_TIME)
        assert original.worker_id == worker_a.worker_id
        assert original.job.execution.run_id == run_id
        assert original.job.execution.subject_id == task_id

        runtime.detach_worker(worker_a.worker_id)
        reconciled = await runtime.reconcile(
            now=_RUNTIME_BASE_TIME + timedelta(seconds=1),
        )
        lost = next(
            record for record in reconciled if record.job.worker_job_id == job.worker_job_id
        )
        assert lost.state is DispatchState.LOST

        fenced = await runtime.fence_for_failover(
            job.worker_job_id,
            now=_RUNTIME_BASE_TIME + timedelta(seconds=2),
        )
        assert fenced.state is DispatchState.FENCED
        assert fenced.job.execution.run_id == run_id
        assert fenced.job.execution.subject_id == task_id

        restored = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        restored_record = restored.get_record(job.worker_job_id)
        assert restored_record.state is DispatchState.FENCED
        assert restored_record.job.execution.run_id == run_id
        assert restored_record.job.execution.subject_id == task_id

        restored.register(
            RegistrationRequest(node=node_b, workers=(worker_b,)),
            now=_RUNTIME_BASE_TIME + timedelta(seconds=3),
        )
        restored.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        replacement = await restored.redispatch_fenced(
            job.worker_job_id,
            now=_RUNTIME_BASE_TIME + timedelta(seconds=3),
        )

        assert replacement.worker_id == worker_b.worker_id
        assert replacement.worker_id != original.worker_id
        assert replacement.job.worker_job_id == job.worker_job_id
        assert replacement.job.execution.run_id == run_id
        assert replacement.job.execution.subject_id == task_id
        assert replacement.job.execution.context.correlation_id == task_id
        assert replacement.job.execution.context.causation_id == run_id
        assert lifecycle_b.start_calls[0].run_id == run_id
        assert lifecycle_b.start_calls[0].subject_id == task_id

    asyncio.run(scenario())


def test_optional_backend_packages_are_not_mandatory_runtime_dependencies() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    dependency_names = {
        re.split(r"[<>=!~\[; ]", dependency.lower(), maxsplit=1)[0].replace("_", "-")
        for dependency in dependencies
    }

    assert dependency_names.isdisjoint(_MANDATORY_DEPENDENCY_DENYLIST)
