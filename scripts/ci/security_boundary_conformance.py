#!/usr/bin/env python3
"""Validate and execute the #1233 production security boundary conformance matrix."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "conformance/security/production_boundary_matrix.toml"

REQUIRED_SURFACES = frozenset(
    {
        "control-plane-mutations",
        "capability-execution",
        "mcp-calls",
        "browser-network-actions",
        "terminal-process-execution",
        "repository-git-operations",
        "connector-actions",
        "plugin-actions",
        "application-actions",
        "local-executor-dispatch",
        "remote-worker-dispatch",
        "marketplace-lifecycle",
        "automation-triggered-actions",
        "import-export",
        "secret-configuration-references",
        "approval-gated-operations",
        "verification-review-transitions",
    }
)
BOUNDARY_FIELDS = (
    "authentication_boundary",
    "authorization_boundary",
    "approval_boundary",
    "scope_boundary",
    "secret_boundary",
    "audit_boundary",
    "failure_boundary",
    "fail_closed_boundary",
    "public_error_boundary",
    "provider_identity_boundary",
)
EVIDENCE_FIELDS = (
    "allow_evidence",
    "deny_evidence",
    "approval_evidence",
    "scope_evidence",
    "secret_evidence",
    "failure_evidence",
    "identity_evidence",
    "audit_evidence",
)
REQUIRED_GLOBAL_REGRESSIONS = frozenset(
    {
        "missing_or_expired_authentication",
        "cross_project_or_workspace_access",
        "revoked_permissions",
        "missing_or_stale_approval",
        "secret_redaction",
        "provider_failure",
        "unavailable_policy_dependency",
        "forged_external_or_provider_identifiers",
        "replay_or_idempotency",
        "remote_worker_identity_mismatch",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or execute the production security boundary conformance matrix."
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Path to the machine-readable #1233 security matrix.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate matrix structure and referenced pytest node IDs without running them.",
    )
    return parser


def _load_matrix(path: Path) -> dict[str, object]:
    if not path.is_absolute():
        path = ROOT / path
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot load security boundary matrix {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("security boundary matrix root must be a TOML table")
    return document


def _require_nonempty_string(mapping: Mapping[str, object], field: str, *, owner: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner}: {field} must be a non-empty string")
    return value


def _evidence_list(
    mapping: Mapping[str, object],
    field: str,
    *,
    owner: str,
    required: bool = False,
) -> tuple[str, ...]:
    value = mapping.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{owner}: {field} must be a TOML array")
    if required and not value:
        raise ValueError(f"{owner}: {field} must contain at least one evidence node")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{owner}: {field} must contain only non-empty pytest node IDs")
    return tuple(value)


def _test_functions(path: Path, cache: dict[Path, frozenset[str]]) -> frozenset[str]:
    cached = cache.get(path)
    if cached is not None:
        return cached
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ValueError(f"cannot inspect evidence test file {path}: {exc}") from exc
    names = frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))\n        and node.name.startswith("test_")
    )
    cache[path] = names
    return names


def _validate_node_id(node_id: str, cache: dict[Path, frozenset[str]]) -> None:
    path_value, separator, test_name = node_id.partition("::")
    if not separator or not path_value or not test_name or "::" in test_name:
        raise ValueError(
            f"evidence node {node_id!r} must have the exact form path/to/test.py::test_name"
        )
    if "[" in test_name or "]" in test_name:
        raise ValueError(f"parameterized evidence node IDs are not supported: {node_id!r}")
    path = ROOT / path_value
    if not path.is_file():
        raise ValueError(f"evidence test file does not exist: {path_value}")
    if test_name not in _test_functions(path, cache):
        raise ValueError(f"evidence test function does not exist: {node_id}")


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return tuple(ordered)


def validate_matrix(document: Mapping[str, object]) -> tuple[str, ...]:
    if document.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if document.get("issue") != 1233:
        raise ValueError("matrix must be owned by issue #1233")
    threat_model = document.get("threat_model")
    if not isinstance(threat_model, str) or not threat_model.strip():
        raise ValueError("threat_model must name the maintained threat-model document")
    if not (ROOT / threat_model).is_file():
        raise ValueError(f"threat_model does not exist: {threat_model}")

    global_regressions = document.get("global_regressions")
    if not isinstance(global_regressions, dict):
        raise ValueError("global_regressions must be a TOML table")
    actual_regressions = frozenset(global_regressions)
    if actual_regressions != REQUIRED_GLOBAL_REGRESSIONS:
        missing = sorted(REQUIRED_GLOBAL_REGRESSIONS - actual_regressions)
        unexpected = sorted(actual_regressions - REQUIRED_GLOBAL_REGRESSIONS)
        raise ValueError(
            "global_regressions do not match #1233 requirements; "
            f"missing={missing}, unexpected={unexpected}"
        )

    surfaces = document.get("surface")
    if not isinstance(surfaces, list):
        raise ValueError("surface must be an array of TOML tables")
    if any(not isinstance(surface, dict) for surface in surfaces):
        raise ValueError("every surface entry must be a TOML table")

    surface_ids: list[str] = []
    evidence: list[str] = []
    cache: dict[Path, frozenset[str]] = {}

    for key in sorted(REQUIRED_GLOBAL_REGRESSIONS):
        nodes = _evidence_list(\n            global_regressions,\n            key,\n            owner=f"global_regressions.{key}",\n            required=True,\n        )
        evidence.extend(nodes)

    for raw_surface in surfaces:
        surface = raw_surface
        surface_id = _require_nonempty_string(surface, "id", owner="surface")
        owner = f"surface {surface_id}"
        surface_ids.append(surface_id)
        _require_nonempty_string(surface, "owner", owner=owner)
        for field in BOUNDARY_FIELDS:
            _require_nonempty_string(surface, field, owner=owner)
        for field in EVIDENCE_FIELDS:
            nodes = _evidence_list(
                surface,
                field,
                owner=owner,
                required=field in {"allow_evidence", "deny_evidence"},
            )
            evidence.extend(nodes)

    actual_surfaces = frozenset(surface_ids)
    if len(surface_ids) != len(actual_surfaces):
        duplicates = sorted({item for item in surface_ids if surface_ids.count(item) > 1})
        raise ValueError(f"surface IDs must be unique; duplicates={duplicates}")
    if actual_surfaces != REQUIRED_SURFACES:
        missing = sorted(REQUIRED_SURFACES - actual_surfaces)
        unexpected = sorted(actual_surfaces - REQUIRED_SURFACES)
        raise ValueError(
            "surface coverage does not match #1233; "
            f"missing={missing}, unexpected={unexpected}"
        )

    node_ids = _dedupe(evidence)
    for node_id in node_ids:
        _validate_node_id(node_id, cache)
    return node_ids


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        document = _load_matrix(args.matrix)
        node_ids = validate_matrix(document)
    except ValueError as exc:
        print(f"security boundary matrix: invalid: {exc}", file=sys.stderr)
        return 2

    print(
        "security boundary matrix: valid "
        f"({len(REQUIRED_SURFACES)} surfaces, {len(node_ids)} distinct evidence tests)"
    )
    if args.validate_only:
        return 0

    process = subprocess.run(
        (sys.executable, "-m", "pytest", "-q", *node_ids),
        cwd=ROOT,
        check=False,
    )
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
