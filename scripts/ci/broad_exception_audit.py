"""Audit broad exception handling and apply reviewed handler classifications.

The AST scanner core remains intentionally generic.  A normal repository-wide invocation also
validates ``config/error_boundary_classifications.json`` as an exact handler-level review registry.
Explicit ``--root`` scans (used by fixtures and focused investigations) remain registry-independent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import broad_exception_audit_core as core
from error_boundary_registry import apply_review_registry

Finding = core.Finding
scan_path = core.scan_path


def build_parser():
    parser = core.build_parser()
    parser.add_argument(
        "--classification-file",
        type=Path,
        default=None,
        help=(
            "Exact handler-level reviewed classification registry. For the default whole-tree "
            "scan, config/error_boundary_classifications.json is loaded automatically when present."
        ),
    )
    return parser


def _registry_path(args) -> Path | None:
    if args.classification_file is not None:
        return args.classification_file
    if args.root is not None:
        return None
    candidate = args.repository_root / "config" / "error_boundary_classifications.json"
    return candidate if candidate.is_file() else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = args.root or [args.repository_root / "src" / "ai_multi_agent_platform"]
    findings = [
        finding
        for root in roots
        for finding in core.scan_path(root, repository_root=args.repository_root)
    ]
    findings.sort(key=lambda item: (item.file, item.line, item.id))

    try:
        findings = apply_review_registry(findings, _registry_path(args))
    except ValueError as exc:
        print(f"error-boundary classification registry error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        payload = [core._safe_finding_payload(item) for item in findings]
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    elif args.format == "markdown":
        sys.stdout.write(core._markdown(findings))
    else:
        sys.stdout.write(core._text(findings))

    return int(args.check and any(item.severity == "prohibited" for item in findings))


if __name__ == "__main__":
    sys.exit(main())
