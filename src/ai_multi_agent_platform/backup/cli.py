"""Offline operator CLI for backup/restore operations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from ai_multi_agent_platform import __version__

from .service import (
    BackupError,
    create_single_node_backup,
    restore_single_node_backup,
    verify_backup,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            backup_path = create_single_node_backup(
                data_dir=Path(args.data_dir),
                destination=Path(args.destination),
                platform_version=__version__,
                platform_commit=args.platform_commit,
                deployment_metadata={"profile": "single-node"},
                quiesced=args.quiesced,
            )
            print(json.dumps({"backup": str(backup_path), "status": "created"}, sort_keys=True))
            return 0
        if args.command == "verify":
            verification = verify_backup(Path(args.backup))
            print(
                json.dumps(
                    {
                        "backup": str(verification.backup_dir),
                        "status": "valid",
                        "files_checked": verification.files_checked,
                        "bytes_checked": verification.bytes_checked,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "restore":
            restored_data_dir = restore_single_node_backup(
                backup_dir=Path(args.backup),
                target_data_dir=Path(args.target_data_dir),
                expected_platform_version=__version__,
                expected_platform_commit=args.expected_platform_commit,
            )
            print(
                json.dumps(
                    {"data_dir": str(restored_data_dir), "status": "restored"}, sort_keys=True
                )
            )
            return 0
    except BackupError as exc:
        parser.exit(2, f"backup error: {exc}\n")
    raise AssertionError("unreachable")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-backup",
        description="Offline backup/restore operations for the single-node deployment profile",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an atomic checksummed backup")
    create.add_argument("--data-dir", required=True)
    create.add_argument("--destination", required=True)
    create.add_argument(
        "--quiesced",
        action="store_true",
        help="confirm that all writers to the deployment data root are stopped",
    )
    create.add_argument(
        "--platform-commit",
        help="record the exact source build commit in the backup manifest",
    )

    verify = commands.add_parser(
        "verify",
        help="verify manifest schema, scope, checksums and SQLite integrity/schema metadata",
    )
    verify.add_argument("backup")

    restore = commands.add_parser("restore", help="restore into a clean replacement data root")
    restore.add_argument("backup")
    restore.add_argument("--target-data-dir", required=True)
    restore.add_argument(
        "--expected-platform-commit",
        help="optionally require an exact source build commit in addition to platform version",
    )
    return parser
