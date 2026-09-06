"""Operator CLI for release manifests, release gates and upstream discovery."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .adoption import UpdateValidationEvidenceError, load_update_validation_evidence
from .codec import ReleaseManifestError, load_release_manifest
from .discovery import (
    ObservedUpstream,
    UpdateDiscoveryError,
    UpdateDisposition,
    evaluate_update_candidates,
    load_compatibility_inventory,
    load_observations,
    record_reviewed_candidate,
)
from .generator import (
    ReleaseGenerationError,
    generate_release_manifest_from_file,
    write_release_manifest,
)
from .persistence import JsonDiscoveryReportStore, StoredDiscoveryReport
from .providers import discover_git_heads, write_git_discovery_result
from .service import evaluate_release, release_metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-release",
        description="Validate release provenance and evaluate safe release/update state.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    generate = subcommands.add_parser(
        "generate",
        help="Deterministically assemble and validate a release-manifest v2",
    )
    generate.add_argument("--source-commit", required=True)
    generate.add_argument("--input", required=True)
    generate.add_argument("--inventory")
    generate.add_argument("--output", required=True)
    generate.add_argument("--json", action="store_true")

    validate = subcommands.add_parser("validate", help="Validate a release manifest and gates")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--json", action="store_true")

    status = subcommands.add_parser("status", help="Print release/operator metadata")
    status.add_argument("--manifest", required=True)
    status.add_argument("--json", action="store_true")

    git_discovery = subcommands.add_parser(
        "upstream-discover-git",
        help="Create provider-neutral advisory observations from Git remote HEADs",
    )
    git_discovery.add_argument("--inventory")
    git_discovery.add_argument("--observed-at", required=True)
    git_discovery.add_argument("--output", required=True)
    git_discovery.add_argument("--json", action="store_true")

    upstream = subcommands.add_parser(
        "upstream-check",
        help="Compare pinned upstream inventory with advisory observations",
    )
    upstream.add_argument("--inventory")
    upstream.add_argument("--observations")
    upstream.add_argument("--disabled", action="store_true")
    upstream.add_argument("--offline", action="store_true")
    upstream.add_argument("--data-dir")
    upstream.add_argument("--reviewed-at")
    upstream.add_argument("--json", action="store_true")

    adoption = subcommands.add_parser(
        "upstream-adoption-check",
        help="Validate revision-bound evidence before recording a reviewed upstream candidate",
    )
    adoption.add_argument("--inventory")
    adoption.add_argument("--observations", required=True)
    adoption.add_argument("--component", required=True)
    adoption.add_argument("--evidence", required=True)
    adoption.add_argument("--compatibility-status", required=True)
    adoption.add_argument("--reviewed-at", required=True)
    adoption.add_argument("--manual-review-approved", action="store_true")
    adoption.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if args.command == "generate":
        return _generate(args)
    if args.command == "upstream-discover-git":
        return _upstream_discover_git(args)
    if args.command == "upstream-check":
        return _upstream_check(args)
    if args.command == "upstream-adoption-check":
        return _upstream_adoption_check(args)

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


def _generate(args: argparse.Namespace) -> int:
    try:
        manifest = generate_release_manifest_from_file(
            source_commit=str(args.source_commit),
            input_path=str(args.input),
            inventory_path=args.inventory,
        )
        write_release_manifest(manifest, str(args.output))
    except (ReleaseGenerationError, UpdateDiscoveryError) as exc:
        print(f"release manifest generation failed: {exc}", file=sys.stderr)
        return 2
    if bool(args.json):
        print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"generated release manifest: {args.output}")
        print(f"release: {manifest.release_version}")
        print(f"source commit: {manifest.source_commit}")
    return 0


def _upstream_discover_git(args: argparse.Namespace) -> int:
    try:
        inventory = load_compatibility_inventory(args.inventory)
        result = discover_git_heads(inventory, observed_at=str(args.observed_at))
        write_git_discovery_result(result, str(args.output))
    except UpdateDiscoveryError as exc:
        print(f"upstream Git discovery failed: {exc}", file=sys.stderr)
        return 2

    if bool(args.json):
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"wrote {len(result.observations)} upstream observations to {args.output}")
        for component, error in sorted(result.errors.items()):
            print(f"WARNING: {component}: {error}")
    return 5 if result.errors else 0


def _upstream_check(args: argparse.Namespace) -> int:
    if bool(args.disabled) and bool(args.offline):
        print("--disabled and --offline are mutually exclusive", file=sys.stderr)
        return 2
    if bool(args.data_dir) != bool(args.reviewed_at):
        print("--data-dir and --reviewed-at must be supplied together", file=sys.stderr)
        return 2
    try:
        inventory = load_compatibility_inventory(args.inventory)
        observed_at: str | None = None
        observations: tuple[ObservedUpstream, ...] = ()
        if not bool(args.disabled) and not bool(args.offline):
            if not args.observations:
                print(
                    "--observations is required unless --disabled or --offline is used",
                    file=sys.stderr,
                )
                return 2
            observed_at, observations = load_observations(str(args.observations))
        report = evaluate_update_candidates(
            inventory,
            observations,
            observed_at=observed_at,
            enabled=not bool(args.disabled),
            offline=bool(args.offline),
        )
        if args.data_dir:
            JsonDiscoveryReportStore.for_data_dir(str(args.data_dir)).write(
                StoredDiscoveryReport(reviewed_at=str(args.reviewed_at), report=report)
            )
    except UpdateDiscoveryError as exc:
        print(f"upstream discovery input invalid: {exc}", file=sys.stderr)
        return 2

    if bool(args.json):
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"upstream discovery mode: {report.mode.value}")
        for candidate in report.candidates:
            revision = candidate.candidate_revision or "—"
            classifications = ",".join(item.value for item in candidate.classifications) or "none"
            print(
                f"{candidate.component}: {candidate.disposition.value} "
                f"current={candidate.current_revision} candidate={revision} "
                f"classification={classifications}"
            )
            for reason in candidate.reasons:
                print(f"  - {reason}")

    blocked = any(
        candidate.disposition is UpdateDisposition.BLOCKED for candidate in report.candidates
    )
    return 4 if blocked else 0


def _upstream_adoption_check(args: argparse.Namespace) -> int:
    try:
        inventory = load_compatibility_inventory(args.inventory)
        observed_at, observations = load_observations(str(args.observations))
        report = evaluate_update_candidates(
            inventory,
            observations,
            observed_at=observed_at,
        )
        matches = [
            candidate for candidate in report.candidates if candidate.component == args.component
        ]
        if len(matches) != 1:
            raise UpdateDiscoveryError(
                f"expected exactly one candidate for component {args.component!r}"
            )
        candidate = matches[0]
        evidence = load_update_validation_evidence(str(args.evidence))
        updated = record_reviewed_candidate(
            inventory,
            candidate,
            compatibility_status=str(args.compatibility_status),
            reviewed_at=str(args.reviewed_at),
            validation_evidence=evidence,
            manual_review_approved=bool(args.manual_review_approved),
        )
    except (UpdateDiscoveryError, UpdateValidationEvidenceError) as exc:
        print(f"upstream adoption validation failed: {exc}", file=sys.stderr)
        return 2

    if bool(args.json):
        print(
            json.dumps(
                {
                    "candidate": candidate.to_dict(),
                    "validation_evidence": evidence.to_dict(),
                    "resulting_compatibility_inventory": updated.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"upstream candidate validated: {candidate.component}")
        print(f"candidate revision: {candidate.candidate_revision}")
        print("production pin mutation: none")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
