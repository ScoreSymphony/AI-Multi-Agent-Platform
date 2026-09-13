from __future__ import annotations

import ast
from pathlib import Path


def ensure_free(path: str) -> Path:
    target = Path(path)
    if target.exists():
        raise SystemExit(f"Target already exists: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def move_with_provenance(source: str, target: str, provenance: str) -> None:
    src = Path(source)
    if not src.exists():
        raise SystemExit(f"Missing migration source: {source}")
    dst = ensure_free(target)
    dst.write_text(
        f'"""{provenance}"""\n\n' + src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    src.unlink()


def split_tests(source: str, outputs: dict[str, set[str]], provenance: str) -> None:
    src = Path(source)
    if not src.exists():
        raise SystemExit(f"Missing split source: {source}")
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    tests = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    assigned = set().union(*outputs.values())
    missing = assigned - tests.keys()
    unassigned = tests.keys() - assigned
    if missing or unassigned:
        raise SystemExit(
            f"Split mismatch for {source}: missing={sorted(missing)} "
            f"unassigned={sorted(unassigned)}"
        )
    first_test_line = min(node.lineno for node in tests.values())
    header = "".join(lines[: first_test_line - 1]).rstrip() + "\n\n"
    for target, names in outputs.items():
        dst = ensure_free(target)
        chunks: list[str] = []
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names
            ):
                assert node.end_lineno is not None
                chunks.append("".join(lines[node.lineno - 1 : node.end_lineno]).rstrip())
        body = (
            f'"""{provenance}"""\n\n'
            "# ruff: noqa: F401\n\n"
            + header
            + "\n\n\n".join(chunks)
            + "\n"
        )
        dst.write_text(body, encoding="utf-8")
    src.unlink()


def replace_exact(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one reference in {path}, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


split_tests(
    "tests/test_security_baseline.py",
    {
        "tests/unit/security/test_security_primitives.py": {
            "test_resolve_within_rejects_parent_traversal_and_absolute_paths",
            "test_resolve_within_rejects_symlink_escape_when_supported",
            "test_redaction_recursively_removes_sensitive_values",
            "test_secret_reference_serializes_without_plaintext_secret_material",
            "test_validate_untrusted_json_rejects_non_json_and_non_finite_values",
            "test_validate_untrusted_json_enforces_resource_bounds",
            "test_security_decision_is_deny_by_default",
            "test_adapter_private_metadata_cannot_grant_authority",
            "test_optional_adapter_absence_does_not_change_canonical_security_decision",
        },
        "tests/integration/security/test_executor_confinement.py": {
            "test_reference_executor_rejects_workspace_traversal",
            "test_reference_executor_rejects_artifact_symlink_escape_when_supported",
        },
    },
    "Migrated from root-level security baseline coverage under #722.",
)

split_tests(
    "tests/test_issue_15_hardening.py",
    {
        "tests/integration/security/test_authorization_hardening.py": {
            "test_refined_issue_13_data_paths_cannot_bypass_authorization",
            "test_control_plane_bridge_uses_canonical_vocabulary_and_resumes_after_approval",
            "test_control_plane_create_approval_cannot_be_reused_for_changed_payload",
        },
        "tests/contract/security/test_control_plane_authorization_hardening_vocabulary.py": {
            "test_control_plane_vocabulary_mapping_is_platform_owned",
        },
    },
    "Migrated under #722; original coverage tracked issue #15.",
)

move_with_provenance(
    "tests/test_issue_14_authorization_vocabulary.py",
    "tests/contract/security/test_distributed_authorization_vocabulary.py",
    "Migrated under #722; original coverage tracked issue #14.",
)
move_with_provenance(
    "tests/test_issue75_authorization.py",
    "tests/contract/security/test_notification_authorization_vocabulary.py",
    "Migrated under #722; original coverage tracked issue #75.",
)
move_with_provenance(
    "tests/test_issue_241_authorization_vocabulary.py",
    "tests/contract/security/test_automation_authorization_vocabulary.py",
    "Migrated under #722; original coverage tracked issue #241.",
)
move_with_provenance(
    "tests/test_issue_384_control_plane_authorization.py",
    "tests/integration/security/test_coordination_control_plane_authorization.py",
    "Migrated under #722; original coverage tracked issue #384.",
)
move_with_provenance(
    "tests/test_issue_87_cross_org_authorization.py",
    "tests/integration/security/test_cross_organization_authorization.py",
    "Migrated under #722; original coverage tracked issue #87.",
)

replace_exact(
    "docs/ADAPTER_SUPPORT_MATRIX.toml",
    'evidence = ["docs/PLATFORM_CONFORMANCE.md", "tests/test_security_baseline.py"]',
    'evidence = ["docs/PLATFORM_CONFORMANCE.md", "tests/unit/security/test_security_primitives.py", "tests/integration/security/test_executor_confinement.py"]',
)
replace_exact(
    "docs/SECURITY_THREAT_MODEL.md",
    "`tests/test_security_baseline.py` establishes regression fixtures for currently implementable invariants:",
    "`tests/unit/security/test_security_primitives.py` and `tests/integration/security/test_executor_confinement.py` establish regression fixtures for currently implementable invariants:",
)
replace_exact(
    "src/ai_multi_agent_platform/conformance/optional_profiles.py",
    '"tests/test_issue_384_control_plane_authorization.py::"',
    '"tests/integration/security/test_coordination_control_plane_authorization.py::"',
)
