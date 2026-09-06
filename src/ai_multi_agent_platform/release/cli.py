"""Operator CLI for release manifests and release gates."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .codec import ReleaseManifestError, load_release_manifest
from .service import evaluate_release, release_metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-release",
        description="Validate release provenance and evaluate safe-release gates.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="Validate a release manifest and gates")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--json", action="store_true")

    status = subcommands.add_parser("status", help="Print release/operator metadata")
    status.add_argument("--manifest", required=True)
    status.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        manifest = load_release_manifest(str(args.manifest))
    except ReleaseManifestError as exc:
        print(f"release manifest invalid: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        report = evaluate_release(manifest)
        if bool(args.json):
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            state = "ready" if report.ready else "blocked"
            print(f"release {report.release_version}: {state}")
            for blocker in report.blockers:
                print(f"BLOCKER: {blocker}")
            for warning in report.warnings:
                print(f"WARNING: {warning}")
        return 0 if report.ready else 3

    if args.command == "status":
        payload = release_metadata(manifest)
        report = evaluate_release(manifest)
        if bool(args.json):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"platform release: {manifest.release_version}")
            print(f"release ready: {str(payload['release_ready']).lower()}")
            for upstream in manifest.upstreams:
                print(
                    f"upstream: {upstream.component} revision={upstream.revision} "
                    f"verified={upstream.last_verified_at}"
                )
            for blocker in report.blockers:
                print(f"blocker: {blocker}")
        return 0

    raise AssertionError(f"unhandled release command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
