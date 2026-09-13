from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from apply_test_migration_722 import rewrite_exact_paths, shift_file_ancestor_references

TESTS = Path("tests")

WHOLE_MOVES: dict[str, str] = {
    # Performance/reference-host cohort: one coherent benchmark responsibility per file.
    "tests/performance/test_issue_440_operating_envelope_catalog.py": (
        "tests/performance/evaluation/test_operating_envelope_catalog.py"
    ),
    "tests/performance/test_issue_440_reference_host_absolute_budget.py": (
        "tests/performance/evaluation/test_reference_host_absolute_budget.py"
    ),
    "tests/performance/test_issue_440_reference_host_campaign.py": (
        "tests/performance/evaluation/test_reference_host_campaign.py"
    ),
    "tests/performance/test_issue_440_reference_host_regression.py": (
        "tests/performance/evaluation/test_reference_host_regression.py"
    ),
    # Cohesive files that looked mixed only because they exercise cross-cutting concerns.
    "tests/test_issue_33_completion_hardening.py": (
        "tests/regression/agents/test_agent_completion_hardening.py"
    ),
    "tests/test_issue_591_egress_policy.py": "tests/integration/security/test_egress_policy.py",
    "tests/test_issue_595_governed_learning.py": (
        "tests/integration/evaluation/test_governed_learning.py"
    ),
    "tests/test_issue_596_compensation.py": (
        "tests/integration/workflows/test_workflow_compensation.py"
    ),
    "tests/test_issue_78_reopened_materialization.py": (
        "tests/regression/templates/test_template_materialization_regression.py"
    ),
    "tests/test_issue_799_component_setup.py": (
        "tests/integration/onboarding/test_component_setup.py"
    ),
    "tests/test_issue_799_component_setup_service.py": (
        "tests/integration/onboarding/test_component_setup_service.py"
    ),
    "tests/test_issue_8_completion_hardening.py": (
        "tests/regression/upstreams/test_hermes_adapter_hardening.py"
    ),
    # Explicit behavior names for destination collisions from the safe pass.
    "tests/test_issue39_single_node_deployment.py": (
        "tests/integration/deployment/test_single_node_deployment_runtime.py"
    ),
    "tests/test_issue40_backup_restore.py": (
        "tests/integration/recovery/test_backup_restore_durable_state.py"
    ),
    "tests/test_issue75_persistence.py": (
        "tests/integration/notifications/test_notification_sqlite_persistence.py"
    ),
    "tests/test_issue75_task_management.py": (
        "tests/integration/notifications/test_task_attention_notifications.py"
    ),
    "tests/test_issue_14_control_plane.py": (
        "tests/integration/distributed/test_distributed_control_plane.py"
    ),
    "tests/test_issue_18_single_node_deployment.py": (
        "tests/integration/automation/test_automation_single_node_persistence.py"
    ),
    "tests/test_issue_20_control_plane.py": (
        "tests/integration/plugins/test_plugin_control_plane.py"
    ),
    "tests/test_issue_33_control_plane.py": (
        "tests/integration/agents/test_agent_control_plane.py"
    ),
    "tests/test_issue_33_persistence.py": (
        "tests/integration/agents/test_agent_repository_persistence.py"
    ),
    "tests/test_issue_366_single_node.py": (
        "tests/integration/capability_assignments/test_capability_assignment_single_node.py"
    ),
    "tests/test_issue_384_backup_restore.py": (
        "tests/integration/recovery/test_coordination_backup_restore.py"
    ),
    "tests/test_issue_439_single_node.py": (
        "tests/integration/planning/test_planning_single_node.py"
    ),
    "tests/test_issue_598_single_node_integration.py": (
        "tests/integration/search/test_decision_record_single_node_integration.py"
    ),
    "tests/test_issue_718_memory_portability.py": (
        "tests/contract/memory/test_memory_portability_schema_compatibility.py"
    ),
    "tests/test_issue_72_control_plane.py": (
        "tests/integration/context/test_conversation_control_plane.py"
    ),
    "tests/test_issue_72_single_node_deployment.py": (
        "tests/e2e/context/test_conversation_single_node_restart.py"
    ),
    "tests/test_issue_78_control_plane.py": (
        "tests/integration/templates/test_template_control_plane.py"
    ),
    "tests/test_issue_78_persistence.py": (
        "tests/integration/templates/test_template_repository_persistence.py"
    ),
    "tests/test_issue_799_single_node_integration.py": (
        "tests/integration/onboarding/test_component_setup_single_node.py"
    ),
    "tests/test_issue_79_memory_portability.py": (
        "tests/integration/memory/test_memory_portability_scope_security.py"
    ),
    "tests/test_issue_86_control_plane.py": (
        "tests/integration/verification/test_verification_control_plane.py"
    ),
    "tests/test_issue_86_observability.py": (
        "tests/integration/verification/test_verification_observability.py"
    ),
    "tests/test_issue_86_persistence.py": (
        "tests/integration/verification/test_verification_sqlite_persistence.py"
    ),
    "tests/test_issue_872_control_plane.py": (
        "tests/integration/verification/test_canonical_verification_control_plane.py"
    ),
    "tests/test_issue_872_observability.py": (
        "tests/integration/verification/test_canonical_verification_observability.py"
    ),
    "tests/test_issue_87_control_plane.py": (
        "tests/integration/organizations/test_organization_control_plane.py"
    ),
    "tests/test_task_management.py": (
        "tests/integration/task_management/test_task_management_control_plane.py"
    ),
    # This number is historical PR provenance, not behavior. Keep provenance in Git history.
    "tests/integration/agents/test_pr_300_semantics.py": (
        "tests/integration/agents/test_conversation_canonical_semantics.py"
    ),
}

SPLITS = {
    "tests/test_issue_251_lifecycle_commands.py": {
        "helper": "tests/data_lifecycle_command_cases.py",
        "helpers": ("_request_context", "_data_context", "_providers"),
        "groups": {
            "tests/integration/memory/test_memory_lifecycle_commands.py": (
                "test_memory_create_promote_update_delete_lifecycle",
                "test_memory_exact_expiry_requires_due_entry_and_exact_scope",
                "test_memory_update_cannot_reclassify_origin_or_scope",
                "test_memory_promote_rejects_non_short_term_source",
            ),
            "tests/integration/knowledge/test_knowledge_lifecycle_commands.py": (
                "test_knowledge_register_update_ingest_reindex_detach_lifecycle",
                "test_knowledge_delete_preserves_canonical_source_tombstone",
            ),
        },
    },
    "tests/test_issue_251_reference_lifecycle.py": {
        "helper": "tests/data_reference_lifecycle_cases.py",
        "helpers": ("_context", "_memory_entry"),
        "groups": {
            "tests/integration/memory/test_memory_reference_lifecycle.py": (
                "test_memory_origin_migrates_existing_database_and_survives_restart",
                "test_organization_memory_rejects_mismatched_organization_owner",
            ),
            "tests/integration/knowledge/test_knowledge_reference_lifecycle.py": (
                "test_local_knowledge_provider_exposes_canonical_source_discovery",
                "test_knowledge_source_revision_and_query_survive_provider_restart",
                "test_knowledge_source_discovery_preserves_project_isolation",
                "test_knowledge_reindex_failure_preserves_source_metadata_and_marks_failed",
                "test_pre_lifecycle_knowledge_provider_degrades_explicitly",
            ),
        },
    },
    "tests/test_issue_72_completion_audit.py": {
        "helper": "tests/conversation_completion_cases.py",
        "helpers": (
            "_collect",
            "_message",
            "_response_request",
            "_data_context",
            "_registry",
            "_register_model",
            "_agent_profile",
        ),
        "groups": {
            "tests/integration/task_management/test_conversation_task_handoff.py": (
                "test_agent_conversation_task_handoff_inherits_exact_assignment",
                "test_team_conversation_task_handoff_inherits_exact_assignment",
            ),
            "tests/integration/context/test_resource_reference_context.py": (
                "test_file_reference_is_resolved_into_ephemeral_model_context",
                "test_binary_file_reference_is_metadata_only_even_when_bytes_are_utf8",
                "test_knowledge_reference_is_resolved_into_ephemeral_model_context",
            ),
            "tests/integration/models/test_agent_conversation_routing.py": (
                "test_agent_conversation_applies_routing_profile_and_fallback",
            ),
        },
    },
}


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def node_source(lines: list[str], node: ast.AST) -> str:
    start = getattr(node, "lineno") - 1
    end = getattr(node, "end_lineno")
    return "".join(lines[start:end]).rstrip() + "\n"


def top_level_imports(tree: ast.Module, lines: list[str]) -> str:
    nodes = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    return "\n".join(node_source(lines, node).rstrip() for node in nodes) + "\n"


def named_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise KeyError(name)


def render_helper(source: Path, helper: Path, helper_names: tuple[str, ...]) -> None:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    body = [top_level_imports(tree, lines).rstrip(), ""]
    for name in helper_names:
        body.extend([node_source(lines, named_function(tree, name)).rstrip(), ""])
    helper.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")


def render_split_test(
    source: Path,
    destination: Path,
    helper: Path,
    helper_names: tuple[str, ...],
    test_names: tuple[str, ...],
) -> None:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    helper_module = helper.stem
    helper_import = f"from {helper_module} import ({', '.join(helper_names)})"
    body = [top_level_imports(tree, lines).rstrip(), helper_import, ""]
    for name in test_names:
        body.extend([node_source(lines, named_function(tree, name)).rstrip(), ""])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    depth_delta = len(destination.parent.parts) - len(source.parent.parts)
    shift_file_ancestor_references(destination, depth_delta)


def apply_whole_moves() -> dict[str, str]:
    mapping: dict[str, str] = {}
    destinations = list(WHOLE_MOVES.values())
    if len(destinations) != len(set(destinations)):
        raise RuntimeError("Residual migration contains duplicate destinations")
    for source_text, destination_text in WHOLE_MOVES.items():
        source = Path(source_text)
        destination = Path(destination_text)
        if not source.is_file():
            raise FileNotFoundError(source)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        run("git", "mv", source.as_posix(), destination.as_posix())
        shift_file_ancestor_references(
            destination,
            len(destination.parent.parts) - len(source.parent.parts),
        )
        mapping[source_text] = destination_text
    return mapping


def apply_splits() -> None:
    for source_text, spec in SPLITS.items():
        source = Path(source_text)
        helper = Path(spec["helper"])
        helper_names = tuple(spec["helpers"])
        groups = dict(spec["groups"])
        if not source.is_file():
            raise FileNotFoundError(source)
        if helper.exists():
            raise FileExistsError(helper)
        helper.parent.mkdir(parents=True, exist_ok=True)
        render_helper(source, helper, helper_names)
        for destination_text, test_names in groups.items():
            destination = Path(destination_text)
            if destination.exists():
                raise FileExistsError(destination)
            render_split_test(
                source,
                destination,
                helper,
                helper_names,
                tuple(test_names),
            )
        run("git", "rm", source.as_posix())


def assert_no_unexpected_historical_names() -> None:
    issue_named = sorted(TESTS.rglob("test_issue*.py"))
    if issue_named:
        joined = "\n".join(path.as_posix() for path in issue_named)
        raise RuntimeError(f"Issue-named tests remain:\n{joined}")
    root_tests = sorted(TESTS.glob("test_*.py"))
    if root_tests:
        joined = "\n".join(path.as_posix() for path in root_tests)
        raise RuntimeError(f"Root-level tests remain:\n{joined}")
    numeric = sorted(
        path
        for path in TESTS.rglob("test_*.py")
        if any(char.isdigit() for char in path.stem)
    )
    allowed = {
        "test_hermes_v0_21_1_pinned_completion.py",
        "test_hermes_v0_21_1.py",
        "test_hermes_v0_21_1_completion.py",
        "test_hermes_v0_21_1_adoption_evidence.py",
    }
    unexpected = [path for path in numeric if path.name not in allowed]
    if unexpected:
        joined = "\n".join(path.as_posix() for path in unexpected)
        raise RuntimeError(f"Unexpected numeric test names remain:\n{joined}")


def main() -> int:
    mapping = apply_whole_moves()
    rewrite_exact_paths(mapping)
    apply_splits()
    assert_no_unexpected_historical_names()
    print(f"Moved {len(mapping)} residual whole modules")
    print(f"Split {len(SPLITS)} mixed historical modules")
    print("Historical issue/PR test naming residual: 0")
    print("Root-level test modules: 0")
    print("Allowed numeric residuals: Hermes v0.21.1 versioned upstream tests only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
