#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
import tokenize
from io import StringIO
from pathlib import Path

ISSUE_REFERENCE = re.compile(r"(?:\bissue\s+)?#\d+\b", re.IGNORECASE)
PROVENANCE_PREFIXES = ("historical context:", "provenance:")

# These maintained test names used issue identifiers as semantic vocabulary. The
# replacements are explicit so the resulting names continue to describe the
# behavior under test rather than relying on lossy mechanical token deletion.
IDENTIFIER_RENAMES = {
    "test_adapter_support_matrix_covers_issue_904_boundaries": (
        "test_adapter_support_matrix_covers_adapter_boundaries"
    ),
    "test_reviewer_independence_and_exact_revision_are_inherited_from_issue_86": (
        "test_reviewer_independence_and_exact_revision_are_inherited_from_verification_contract"
    ),
    "test_portable_verification_bindings_fail_closed_without_local_issue86_authority": (
        "test_portable_verification_bindings_fail_closed_without_local_verification_authority"
    ),
    "test_issue79_research_codec_preserves_history_and_declares_verification_dependency": (
        "test_research_codec_preserves_history_and_declares_verification_dependency"
    ),
    "test_final_end_to_end_trace_crosses_every_issue_16_layer": (
        "test_final_end_to_end_trace_crosses_every_observability_layer"
    ),
    "test_live_membership_guard_revokes_stale_team_scope_before_issue_15_policy": (
        "test_live_membership_guard_revokes_stale_team_scope_before_authorization_policy"
    ),
    "test_refined_issue_13_data_paths_cannot_bypass_authorization": (
        "test_refined_data_paths_cannot_bypass_authorization"
    ),
    "test_verification_timeline_reader_maps_audit_to_issue_16_semantics": (
        "test_verification_timeline_reader_maps_audit_to_observability_semantics"
    ),
    "test_verification_required_and_changes_requested_use_opaque_issue86_attention_contract": (
        "test_verification_required_and_changes_requested_use_opaque_verification_attention_contract"
    ),
    "test_membership_attention_supports_canonical_organization_scope_without_owning_issue87": (
        "test_membership_attention_supports_canonical_organization_scope_without_owning_organization_lifecycle"
    ),
    "test_approval_resolved_projection_uses_canonical_issue15_status_without_payload": (
        "test_approval_resolved_projection_uses_canonical_approval_status_without_payload"
    ),
    "test_public_workspace_composition_preserves_issue88_upcoming_queue": (
        "test_public_workspace_composition_preserves_task_management_upcoming_queue"
    ),
    "test_public_workspace_openapi_preserves_issue88_query_contract": (
        "test_public_workspace_openapi_preserves_task_management_query_contract"
    ),
    "test_capability_bridge_routes_agent_policy_and_approval_to_issue_15_gate": (
        "test_capability_bridge_routes_agent_policy_and_approval_to_authorization_gate"
    ),
    "test_policy_aware_discovery_filters_denied_without_issue15_backend": (
        "test_policy_aware_discovery_filters_denied_without_authorization_backend"
    ),
}

PHRASE_PATTERNS = (
    re.compile(
        r"\s*\((?:historical(?:ly)?\s+)?(?:issue\s+)?#\d+\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:introduced|added|defined|owned|required|tracked|implemented|requested|established)\s+"
        r"(?:by|in|under|for)\s+(?:issue\s+)?#\d+\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:for|from|under|after|before|via|in|by|of)\s+(?:issue\s+)?#\d+\b",
        re.IGNORECASE,
    ),
)


def _is_provenance_line(line: str) -> bool:
    normalized = line.strip().lstrip("#").strip().strip('"\'').lower()
    return normalized.startswith(PROVENANCE_PREFIXES)


def _clean_issue_text(line: str) -> str:
    if not ISSUE_REFERENCE.search(line) or _is_provenance_line(line):
        return line

    leading = line[: len(line) - len(line.lstrip())]
    body = line[len(leading) :]
    for pattern in PHRASE_PATTERNS:
        body = pattern.sub("", body)
    body = ISSUE_REFERENCE.sub("", body)
    body = re.sub(r"\(\s*\)", "", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\s+([,.;:])", r"\1", body)
    body = re.sub(r"([([{])\s+", r"\1", body)
    body = re.sub(r"\s+([)\]}])", r"\1", body)
    return leading + body


def _docstring_line_numbers(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", ())
        if not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        start = getattr(first.value, "lineno", None)
        end = getattr(first.value, "end_lineno", start)
        if start is not None and end is not None:
            lines.update(range(start, end + 1))
    return lines


def remediate_python(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    docstring_lines = _docstring_line_numbers(tree)
    comment_lines = {
        token.start[0]
        for token in tokenize.generate_tokens(StringIO(source).readline)
        if token.type == tokenize.COMMENT
        and ISSUE_REFERENCE.search(token.string)
        and not _is_provenance_line(token.string)
    }
    target_lines = docstring_lines | comment_lines

    lines = source.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines, start=1):
        if index not in target_lines:
            continue
        ending = "\n" if line.endswith("\n") else ""
        raw = line[:-1] if ending else line
        cleaned = _clean_issue_text(raw)
        if cleaned != raw:
            lines[index - 1] = cleaned + ending
            changed = True

    updated = "".join(lines)
    for old, new in IDENTIFIER_RENAMES.items():
        if old in updated:
            updated = updated.replace(old, new)
            changed = True

    if not changed:
        return False

    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove legacy issue-number semantics from maintained Python docstrings/comments."
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repository_root.resolve()

    changed: list[str] = []
    for root_name in ("src", "tests", "scripts"):
        base = root / root_name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root)
            if relative.parts[:2] == ("tests", "evidence"):
                continue
            if remediate_python(path):
                changed.append(relative.as_posix())

    print(f"remediated_files={len(changed)}")
    for path in changed:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
