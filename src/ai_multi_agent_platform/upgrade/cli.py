"""Offline operator CLI for validated platform upgrades."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import TextIO

from .compatibility import FormatTranslatorRegistry
from .coordination import CoordinatorAwareMigrationRunner, CoordinatorAwareUpgradePreflight
from .migrations import JsonMigrationHistoryStore, MigrationError, MigrationRegistry
from .models import VersionSnapshot
from .preflight import PreflightRequest
from .service import JsonUpgradeHistoryStore, MaintenanceStateStore, UpgradeError, UpgradeService
from .versioning import JsonVersionStateStore, VersionStateError, current_release_versions


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv)


def run_cli(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    try:
        data_dir = Path(args.data_dir).expanduser().resolve()
        if args.command == "initialize":
            if not data_dir.is_dir():
                raise UpgradeError(f"data directory does not exist: {data_dir}")
            snapshot = JsonVersionStateStore.for_data_dir(data_dir).initialize()
            _write(out, {"status": "initialized", "versions": snapshot.to_dict()}, args.json)
            return 0

        state = JsonVersionStateStore.for_data_dir(data_dir)
        current = state.read()
        registry = default_migration_registry()
        history = JsonMigrationHistoryStore.for_data_dir(data_dir)

        if args.command == "versions":
            target = _target_snapshot(current, registry)
            _write(
                out,
                {"installed": current.to_dict(), "release": target.to_dict()},
                args.json,
            )
            return 0
        if args.command == "history":
            _write(out, {"records": [item.to_dict() for item in history.records()]}, args.json)
            return 0
        if args.command == "maintenance":
            active = MaintenanceStateStore.for_data_dir(data_dir).active()
            _write(out, {"maintenance": active}, args.json)
            return 1 if active else 0

        target = _target_snapshot(current, registry)
        portable = FormatTranslatorRegistry(target.portable_format)
        template = FormatTranslatorRegistry(target.template_schema)
        preflight = CoordinatorAwareUpgradePreflight(
            registry,
            history,
            portable_translators=portable,
            template_translators=template,
        )
        request = PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=target,
            backup_dir=Path(args.backup_dir).expanduser().resolve() if args.backup_dir else None,
            historical_event_schema_versions=frozenset(args.historical_event_version),
            portable_package_versions=frozenset(args.portable_version),
            template_package_versions=frozenset(args.template_version),
            minimum_free_bytes=args.minimum_free_bytes,
            resume_failed=args.resume_failed,
        )
        if args.command == "preflight":
            report = preflight.run(request)
            _write(out, report.to_dict(), args.json)
            return 0 if report.ok else 2
        if args.command == "apply":
            service = UpgradeService(
                migrations=registry,
                runner=CoordinatorAwareMigrationRunner(history),
                preflight=preflight,
                version_state=state,
                maintenance=MaintenanceStateStore.for_data_dir(data_dir),
                history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
            )
            result = service.apply(
                request,
                quiesced=args.quiesced,
                resume_failed=args.resume_failed,
            )
            _write(out, {"status": "upgraded", **result.to_dict()}, args.json)
            return 0
        raise UpgradeError(f"unsupported command: {args.command}")
    except (UpgradeError, MigrationError, VersionStateError, OSError, ValueError) as exc:
        _write_error(err, str(exc), getattr(args, "json", False))
        return 2


def default_migration_registry() -> MigrationRegistry:
    """Production migration chain for this release.

    0.0.1 establishes the framework and baseline marker, so it intentionally has no synthetic
    production migration. Future releases add immutable MigrationStep entries here and prove them
    against an older-release fixture before release.
    """

    return MigrationRegistry()


def _target_snapshot(current: VersionSnapshot, registry: MigrationRegistry) -> VersionSnapshot:
    release = current_release_versions(
        migration_revision=current.migration_revision,
        adapter_versions=current.adapter_versions,
        plugin_interface_versions=current.plugin_interface_versions,
    )
    steps = registry.plan(current.domain_schema, release.domain_schema)
    if steps:
        release = replace(release, migration_revision=steps[-1].revision)
    return release


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-upgrade",
        description="Offline upgrade preflight and migration lifecycle",
    )
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("initialize", help="adopt this release as the explicit upgrade baseline")
    commands.add_parser("versions", help="show installed and target version dimensions")
    commands.add_parser("history", help="show deterministic migration history")
    commands.add_parser("maintenance", help="report durable upgrade-maintenance state")
    for name in ("preflight", "apply"):
        command = commands.add_parser(name, help=f"{name} the supported upgrade path")
        command.add_argument("--backup-dir")
        command.add_argument("--minimum-free-bytes", type=int, default=0)
        command.add_argument("--portable-version", action="append", default=[])
        command.add_argument("--template-version", action="append", default=[])
        command.add_argument("--historical-event-version", action="append", default=[])
        command.add_argument("--resume-failed", action="store_true")
        if name == "apply":
            command.add_argument(
                "--quiesced",
                action="store_true",
                help="assert new dispatch is paused and active work has been drained/cancelled",
            )
    return parser


def _write(stream: TextIO, value: object, compact: bool) -> None:
    if compact:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_error(stream: TextIO, message: str, compact: bool) -> None:
    _write(
        stream,
        {"error": {"code": "upgrade_failed", "message": message, "retryable": False}},
        compact,
    )
