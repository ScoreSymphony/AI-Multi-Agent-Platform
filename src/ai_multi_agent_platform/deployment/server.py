"""Operator entrypoint for the Stage-1 single-node self-hosted profile."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.backup import (
    RESTORE_RECOVERY_DIR,
    RESTORE_RECOVERY_REPORT,
    PostRestoreRecoveryResult,
    RestoreValidationError,
    reconcile_restored_single_node,
    validate_restored_single_node,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import RecoveryReport

from .config import SingleNodeConfig, load_single_node_config
from .restore_integrity import single_node_restore_integrity_validators
from .single_node import SingleNodeDeployment, build_single_node_deployment

DeploymentBuilder = Callable[[SingleNodeConfig], SingleNodeDeployment]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-server",
        description="Run or bootstrap the AI Multi-Agent Platform single-node profile.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("serve", help="Start the authenticated single-node Control Plane")
    subcommands.add_parser(
        "smoke",
        help="Run a retry-safe canonical Task/Run through the local reference execution path",
    )
    subcommands.add_parser(
        "recover-restore",
        help="Run required post-restore recovery and readiness validation without serving",
    )

    resolve = subcommands.add_parser(
        "resolve-restore-run",
        help="Resolve one orphaned restored Run while normal serving is blocked",
    )
    resolve.add_argument("--task-id", required=True)
    resolve.add_argument("--run-id", required=True)
    resolve.add_argument(
        "--outcome",
        required=True,
        choices=(RunStatus.CANCELLED.value, RunStatus.FAILED.value),
        help="Canonical terminal outcome recorded after operator investigation",
    )
    resolve.add_argument(
        "--reason",
        required=True,
        help="Human-readable reason recorded in the canonical recovery outcome",
    )

    bootstrap = subcommands.add_parser(
        "bootstrap-admin",
        help="Create/recover the first local user and its explicit administrator policy",
    )
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from one line on stdin instead of an interactive prompt",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    deployment_builder: DeploymentBuilder = build_single_node_deployment,
) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = load_single_node_config()
    deployment = deployment_builder(config)

    if args.command == "bootstrap-admin":
        password = _read_password(password_stdin=bool(args.password_stdin))
        account = deployment.bootstrap_admin(str(args.username), password)
        print(f"bootstrapped administrator identity: {account.user_id}")
        return 0

    if args.command == "smoke":
        result = asyncio.run(deployment.run_reference_smoke())
        print(
            "single-node smoke succeeded: "
            f"task={result.task_id} run={result.run_id} "
            f"task_status={result.task_status.value} run_status={result.run_status.value}"
        )
        return 0

    if args.command == "resolve-restore-run":
        try:
            return asyncio.run(
                _resolve_restore_run(
                    deployment,
                    task_id=str(args.task_id),
                    run_id=str(args.run_id),
                    outcome=RunStatus(str(args.outcome)),
                    reason=str(args.reason),
                )
            )
        except (ContractError, RestoreValidationError, RuntimeError, ValueError) as exc:
            print(f"restore run resolution failed: {exc}", file=sys.stderr)
            return 3

    if args.command in {"recover-restore", "serve"}:
        try:
            recovery = asyncio.run(_run_restore_recovery(deployment))
        except (RestoreValidationError, RuntimeError) as exc:
            print(f"post-restore recovery blocked: {exc}", file=sys.stderr)
            return 3
        _print_restore_recovery(recovery)
        if recovery is not None and not recovery.ready_for_service:
            print(
                "post-restore recovery remains blocked: "
                f"unresolved_runs={len(recovery.unresolved_run_ids)} "
                f"report={recovery.report_path}",
                file=sys.stderr,
            )
            return 3
        if args.command == "recover-restore":
            return 0

        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover - exercised by packaging/install smoke
            raise SystemExit(
                "The server extra is required. Install with: pip install '.[server]'"
            ) from exc
        uvicorn.run(
            deployment.app,
            host=config.host,
            port=config.port,
            log_level=config.log_level,
            proxy_headers=False,
        )
        return 0

    raise AssertionError(f"unhandled deployment command: {args.command}")


async def _resolve_restore_run(
    deployment: SingleNodeDeployment,
    *,
    task_id: str,
    run_id: str,
    outcome: RunStatus,
    reason: str,
) -> int:
    """Resolve one Run named by the authoritative blocked restore report, then revalidate."""

    if outcome not in {RunStatus.CANCELLED, RunStatus.FAILED}:
        raise ValueError("restore recovery outcome must be cancelled or failed")
    if not reason.strip():
        raise ValueError("restore recovery reason must not be blank")

    report_path = (
        deployment.config.data_dir.expanduser().resolve()
        / RESTORE_RECOVERY_DIR
        / RESTORE_RECOVERY_REPORT
    )
    _require_blocked_report_target(report_path, task_id=task_id, run_id=run_id)

    run = await deployment.kernel.get_run(task_id, run_id)
    if not run.recovery_required:
        raise RuntimeError(f"run {run_id} is not marked recovery_required")
    if run.status is not RunStatus.RUNNING:
        raise RuntimeError(
            f"run {run_id} cannot be operator-resolved from {run.status.value}; expected running"
        )

    await deployment.kernel.record_run_outcome(
        idempotency_key=f"restore-resolution:{run_id}:{outcome.value}",
        task_id=task_id,
        run_id=run_id,
        status=outcome,
        output={
            "recovery_resolution": reason.strip(),
            "recovery_source": "operator-disaster-recovery",
        },
        actor_ref="operator:disaster-recovery",
        source="operator-disaster-recovery",
    )
    print(f"restored run resolved: task={task_id} run={run_id} outcome={outcome.value}")

    recovery = await _run_restore_recovery(deployment)
    _print_restore_recovery(recovery)
    if recovery is not None and not recovery.ready_for_service:
        print(
            "post-restore recovery remains blocked after resolution: "
            f"unresolved_runs={len(recovery.unresolved_run_ids)} "
            f"report={recovery.report_path}",
            file=sys.stderr,
        )
        return 3
    return 0


def _require_blocked_report_target(report_path: Path, *, task_id: str, run_id: str) -> None:
    if not report_path.is_file():
        raise RuntimeError("no blocked restore recovery report exists")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("restore recovery report is unreadable or invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("report_version") != 1:
        raise RuntimeError("restore recovery report version is incompatible")
    if payload.get("ready_for_service") is True:
        raise RuntimeError("restore recovery is already ready for normal service")

    unresolved = payload.get("unresolved_run_ids")
    if not isinstance(unresolved, list) or run_id not in unresolved:
        raise RuntimeError(f"run {run_id} is not unresolved in the blocked restore report")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise RuntimeError("restore recovery report task details are invalid")
    for task in tasks:
        if not isinstance(task, dict) or task.get("task_id") != task_id:
            continue
        entries = task.get("entries")
        if isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("run_id") == run_id for entry in entries
        ):
            return
    raise RuntimeError(f"run {run_id} does not belong to task {task_id} in restore report")


async def _run_restore_recovery(
    deployment: SingleNodeDeployment,
) -> PostRestoreRecoveryResult | None:
    async def validate(
        reports: tuple[RecoveryReport, ...],
        restore_metadata: dict[str, Any],
    ) -> tuple[str, ...]:
        return await validate_restored_single_node(
            data_dir=deployment.config.data_dir,
            kernel=deployment.kernel,
            scopes=deployment.scopes,
            reports=reports,
            restore_metadata=restore_metadata,
            health_probe=deployment.control_plane.health,
            additional_validators=single_node_restore_integrity_validators(deployment),
        )

    return await reconcile_restored_single_node(
        data_dir=deployment.config.data_dir,
        kernel=deployment.kernel,
        validation=validate,
        retry_blocked=True,
    )


def _print_restore_recovery(recovery: PostRestoreRecoveryResult | None) -> None:
    if recovery is None:
        return
    print(
        "post-restore recovery completed: "
        f"runs_checked={recovery.runs_checked} "
        f"unresolved={len(recovery.unresolved_run_ids)} "
        f"ready={str(recovery.ready_for_service).lower()} "
        f"checks={len(recovery.validation_checks)} report={recovery.report_path}"
    )


def _read_password(*, password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise ValueError("password stdin was empty")
        return password
    return getpass.getpass("Initial administrator password: ")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
