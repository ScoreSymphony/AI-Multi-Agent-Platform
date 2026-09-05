"""Post-restore integrity and readiness checks for the single-node profile."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.kernel import PlatformKernel, RecoveryReport

from .service import BackupError, verify_restored_single_node_data_root

HealthProbe = Callable[[], Awaitable[dict[str, JsonValue]]]
RestoreIntegrityValidator = Callable[[tuple[RecoveryReport, ...]], Awaitable[tuple[str, ...]]]


class RestoreValidationError(RuntimeError):
    """Raised when a restored deployment is not safe to return to normal service."""


async def validate_restored_single_node(
    *,
    data_dir: Path,
    kernel: PlatformKernel,
    scopes: ScopeStore,
    reports: tuple[RecoveryReport, ...],
    restore_metadata: dict[str, Any],
    health_probe: HealthProbe,
    extra_validators: tuple[RestoreIntegrityValidator, ...] = (),
) -> tuple[str, ...]:
    """Validate durable stores, canonical references, file bytes, and provider readiness.

    The generic backup layer owns checks that are common to every single-node composition. The
    concrete deployment can append validators for durable subsystems that it composes (for example
    Agents or Conversations) without making backup core depend on those application modules.
    """

    sqlite_versions = _sqlite_versions_from_restore_metadata(restore_metadata)
    try:
        verify_restored_single_node_data_root(
            data_dir,
            expected_sqlite_user_versions=sqlite_versions,
        )
    except BackupError as exc:
        raise RestoreValidationError(f"restored durable-state integrity failed: {exc}") from exc

    project_ids = {project.id for project in scopes.list_projects()}
    task_count, run_count = await _validate_canonical_references(
        kernel=kernel,
        scopes=scopes,
        reports=reports,
    )
    workspace_count = _validate_workspace_references(scopes=scopes, project_ids=project_ids)
    file_count = _validate_file_store(data_dir=data_dir, project_ids=project_ids)

    checks: list[str] = [
        "durable-state-layout-and-sqlite-integrity",
        f"canonical-task-run-references:{task_count}:{run_count}",
        f"workspace-project-references:{workspace_count}",
        f"durable-file-metadata-and-bytes:{file_count}",
    ]
    for validator in extra_validators:
        try:
            validator_checks = await validator(reports)
        except RestoreValidationError:
            raise
        except Exception as exc:
            raise RestoreValidationError(
                f"deployment restore-integrity validator failed: {type(exc).__name__}: {exc}"
            ) from exc
        checks.extend(validator_checks)

    health = await health_probe()
    if health.get("status") != "healthy" or health.get("ready") is not True:
        raise RestoreValidationError(
            "restored control-plane health/readiness check failed: "
            f"status={health.get('status')!r} ready={health.get('ready')!r}"
        )
    checks.append("control-plane-provider-health-ready")
    return tuple(checks)


def _sqlite_versions_from_restore_metadata(value: dict[str, Any]) -> dict[str, int]:
    schema_migration = value.get("schema_migration")
    if not isinstance(schema_migration, dict):
        raise RestoreValidationError("restore metadata is missing schema_migration")
    versions = schema_migration.get("sqlite_user_versions")
    if not isinstance(versions, dict):
        raise RestoreValidationError("restore metadata is missing sqlite_user_versions")

    normalized: dict[str, int] = {}
    for path, version in versions.items():
        if not isinstance(path, str) or not isinstance(version, int):
            raise RestoreValidationError("restore sqlite_user_versions contains invalid data")
        normalized[path] = version
    return normalized


async def _validate_canonical_references(
    *,
    kernel: PlatformKernel,
    scopes: ScopeStore,
    reports: tuple[RecoveryReport, ...],
) -> tuple[int, int]:
    seen_tasks: set[str] = set()
    run_count = 0
    for report in reports:
        if report.task_id in seen_tasks:
            raise RestoreValidationError(f"duplicate recovered task report: {report.task_id}")
        seen_tasks.add(report.task_id)
        try:
            task = await kernel.get_task(report.task_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"canonical task reference cannot be reconstructed: {report.task_id}"
            ) from exc

        project_id = task.task.project_id
        if project_id is not None:
            try:
                scopes.get_project(project_id)
            except ContractError as exc:
                raise RestoreValidationError(
                    f"task {task.task_id} references missing project {project_id}"
                ) from exc

        for run_id in task.run_ids:
            try:
                run = await kernel.get_run(task.task_id, run_id)
            except ContractError as exc:
                raise RestoreValidationError(
                    f"task {task.task_id} references missing run {run_id}"
                ) from exc
            if run.task_id != task.task_id:
                raise RestoreValidationError(
                    f"run {run_id} correlation does not match task {task.task_id}"
                )
            if run.run.subject_type == "task":
                if run.run.subject_id != task.task_id:
                    raise RestoreValidationError(
                        f"task run {run_id} has invalid subject {run.run.subject_id}"
                    )
            elif run.run.subject_type == "step":
                if task.plan_ref is None or run.run.subject_id not in task.step_ids:
                    raise RestoreValidationError(
                        f"step run {run_id} references missing step {run.run.subject_id}"
                    )
            else:
                raise RestoreValidationError(
                    f"run {run_id} has unsupported subject type {run.run.subject_type!r}"
                )
            run_count += 1
    return len(seen_tasks), run_count


def _validate_workspace_references(*, scopes: ScopeStore, project_ids: set[str]) -> int:
    count = 0
    for workspace in scopes.list_workspaces():
        if workspace.project_id not in project_ids:
            raise RestoreValidationError(
                f"workspace {workspace.id} references missing project {workspace.project_id}"
            )
        count += 1
    return count


def _validate_file_store(*, data_dir: Path, project_ids: set[str]) -> int:
    root = data_dir.expanduser().resolve()
    database = root / "db" / "files.sqlite3"
    if not database.is_file():
        raise RestoreValidationError("restored file metadata database is missing")

    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'data_files'"
            ).fetchone()
            if table is None:
                raise RestoreValidationError("restored file metadata table is missing")
            rows = connection.execute(
                "SELECT file_id, project_id, size_bytes, sha256, state FROM data_files"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("restored file metadata database cannot be read") from exc

    checked = 0
    for row in rows:
        file_id = str(row[0])
        project_id = None if row[1] is None else str(row[1])
        size_bytes = int(row[2])
        sha256 = str(row[3])
        state = str(row[4])

        if project_id is not None and project_id not in project_ids:
            raise RestoreValidationError(f"file {file_id} references missing project {project_id}")
        if state == "pending":
            raise RestoreValidationError(f"file {file_id} remained pending across disaster restore")
        if state == "tombstoned":
            continue
        if state != "ready":
            raise RestoreValidationError(f"file {file_id} has invalid persisted state {state!r}")

        path = root / "files" / file_id
        if not path.is_file():
            raise RestoreValidationError(f"durable file bytes are missing for {file_id}")
        if path.stat().st_size != size_bytes:
            raise RestoreValidationError(f"durable file size mismatch for {file_id}")
        if _sha256(path) != sha256:
            raise RestoreValidationError(f"durable file checksum mismatch for {file_id}")
        checked += 1
    return checked


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
