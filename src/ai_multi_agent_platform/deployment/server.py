"""Operator entrypoint for the Stage-1 single-node self-hosted profile."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable, Sequence
from typing import Any

from ai_multi_agent_platform.backup import (
    PostRestoreRecoveryResult,
    RestoreValidationError,
    reconcile_restored_single_node,
    require_blocked_restore_run,
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
        help="Resolve one orphaned Run named by a blocked disaster-restore report",
    )
    resolve.add_argument("--task-id", required=True)
    resolve.add_argument("--run-id", required=True)
    resolve.add_argument("--resolution", required=True, choices=("failed", "cancelled"))
    resolve.add_argument(
        "--reason",
        required=True,
        help="Operator reason recorded in the canonical terminal Run output",
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
        task_id = str(args.task_id)
        run_id = str(args.run_id)
        resolution = RunStatus(str(args.resolution))
        reason = str(args.reason).strip()
        if not reason:
            print("restore run resolution requires a non-blank reason", file=sys.stderr)
            return 2
        try:
            require_blocked_restore_run(
                config.data_dir,
                task_id=task_id,
                run_id=run_id,
            )
            asyncio.run(
                deployment.kernel.record_run_outcome(
                    idempotency_key=f"restore-resolution:{task_id}:{run_id}:{resolution.value}",
                    task_id=task_id,
                    run_id=run_id,
                    status=resolution,
                    output={
                        "reason": reason,
                        "recovery_resolution": resolution.value,
                    },
                    actor_ref="service:restore-recovery-operator",
                    source="restore-recovery-operator",
                )
            )
            recovery = asyncio.run(_run_restore_recovery(deployment))
        except (ContractError, RestoreValidationError, RuntimeError) as exc:
            print(f"restore run resolution blocked: {exc}", file=sys.stderr)
            return 3
        _print_restore_recovery(recovery)
        if recovery is not None and not recovery.ready_for_service:
            print(
                "post-restore recovery remains blocked after run resolution: "
                f"unresolved_runs={len(recovery.unresolved_run_ids)} "
                f"report={recovery.report_path}",
                file=sys.stderr,
            )
            return 3
        print(
            "restore run resolved: "
            f"task={task_id} run={run_id} resolution={resolution.value}"
        )
        return 0

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
            extra_validators=single_node_restore_integrity_validators(deployment),
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
