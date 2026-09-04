"""Operator entrypoint for the Stage-1 single-node self-hosted profile."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from ai_multi_agent_platform.backup import (
    PostRestoreRecoveryResult,
    reconcile_restored_single_node,
)

from .config import load_single_node_config
from .single_node import build_single_node_deployment


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
        help="Run required post-restore canonical Run recovery without starting the server",
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = load_single_node_config()
    deployment = build_single_node_deployment(config)

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

    if args.command == "recover-restore":
        recovery = asyncio.run(
            reconcile_restored_single_node(data_dir=config.data_dir, kernel=deployment.kernel)
        )
        _print_restore_recovery(recovery)
        return 0

    if args.command == "serve":
        recovery = asyncio.run(
            reconcile_restored_single_node(data_dir=config.data_dir, kernel=deployment.kernel)
        )
        _print_restore_recovery(recovery)
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


def _print_restore_recovery(recovery: PostRestoreRecoveryResult | None) -> None:
    if recovery is None:
        return
    print(
        "post-restore recovery completed: "
        f"runs_checked={recovery.runs_checked} "
        f"unresolved={len(recovery.unresolved_run_ids)} report={recovery.report_path}"
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
