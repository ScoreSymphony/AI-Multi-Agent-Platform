"""Offline operator CLI for backup/restore operations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from ai_multi_agent_platform import __version__

from .service import BackupError, create_single_node_backup, restore_single_node_backup, verify_backup


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_single_node_backup(
                data_dir=Path(args.data_dir),
                destination=Path(args.destination),
                platform_version=__version__,
                platform_commit=args.platform_commit,
                deployment_metadata={"profile": "single-node"},
                quiesced=args.quiesced,
            )
            print(json.dumps({"backup": str(result), "status": "created"}, sort_keys=True))
            return 0
        if args.command == "verify":
            result = verify_backup(Path(args.backup))
            print(
                json.dumps(
                    {
                        "backup": str(result.backup_dir),
                        "status": "valid",
                        "files_checked": result.files_checked,
                        "bytes_checked": result.bytes_checked,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "restore":
            result = restore_single_node_backup(
                backup_dir=Path(args.backup),
                target_data_dir=Path(args.target_data_dir),
                expected_platform_version=__version__,
            )
            print(json.dumps({"data_dir": str(result), "status": "restored"}, sort_keys=True))
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
    create.add_argument("--platform-commit")

    verify = commands.add_parser("verify", help="verify manifest, file set, sizes and checksums")
    verify.add_argument("backup")

    restore = commands.add_parser("restore", help="restore into a clean replacement data root")
    restore.add_argument("backup")
    restore.add_argument("--target-data-dir", required=True)
    return parser
