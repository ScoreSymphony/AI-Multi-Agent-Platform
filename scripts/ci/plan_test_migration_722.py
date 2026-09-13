from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

TESTS = Path("tests")
CANONICAL_SUITES = {
    "unit",
    "contract",
    "integration",
    "e2e",
    "performance",
    "regression",
    "release",
}
ISSUE_NAME = re.compile(r"^test_issue_?(?:\d+_?)+(?P<suffix>.+)\.py$")
OTHER_HISTORICAL = {
    "test_handoff_reference_pinning_651.py": "test_handoff_reference_pinning.py",
    "test_integration_591_650_651_context_egress.py": "test_context_egress_integration.py",
    "test_integration_591_650_651_final_review_regressions.py": "test_context_egress_final_review_regressions.py",
    "test_integration_591_650_651_review_regressions.py": "test_context_egress_review_regressions.py",
}

# These root modules predate the canonical layout and have stable, obvious ownership.
ROOT_OVERRIDES: dict[str, tuple[str, str]] = {
    "test_application_distribution.py": ("integration", "application_distribution"),
    "test_automation.py": ("unit", "automation"),
    "test_browser_capability.py": ("unit", "browser"),
    "test_control_plane_workspaces.py": ("integration", "workspaces"),
    "test_evaluation_aggregation.py": ("unit", "evaluation"),
    "test_evaluation_regression_identity.py": ("regression", "evaluation"),
    "test_evaluation_sqlite_history.py": ("integration", "evaluation"),
    "test_executor_kernel_integration.py": ("integration", "kernel"),
    "test_filesystem_registry_fragments.py": ("unit", "files"),
    "test_forge_http.py": ("integration", "forge"),
    "test_forge_kernel_regressions.py": ("regression", "forge"),
    "test_forge_optionality.py": ("contract", "forge"),
    "test_handoff_reference_pinning_651.py": ("integration", "agents"),
    "test_inference_backend_evaluation.py": ("integration", "evaluation"),
    "test_integration_591_650_651_context_egress.py": ("integration", "context"),
    "test_integration_591_650_651_final_review_regressions.py": ("regression", "context"),
    "test_integration_591_650_651_review_regressions.py": ("regression", "context"),
    "test_kernel.py": ("unit", "kernel"),
    "test_kernel_consistency.py": ("regression", "kernel"),
    "test_message_transport.py": ("integration", "distributed"),
    "test_package.py": ("unit", "packaging"),
    "test_plugin_discovery_and_state.py": ("integration", "plugins"),
    "test_plugins.py": ("unit", "plugins"),
    "test_portability.py": ("integration", "portability"),
    "test_portability_connectors.py": ("integration", "portability"),
    "test_run_workspace_binding_restart.py": ("integration", "workspaces"),
    "test_run_workspace_bindings.py": ("integration", "workspaces"),
    "test_skillspector_benchmark_semantics.py": ("performance", "skills"),
    "test_skillspector_container_command.py": ("integration", "skills"),
    "test_skillspector_evaluation.py": ("integration", "skills"),
    "test_task_management.py": ("integration", "task_management"),
    "test_workspace_persistence.py": ("integration", "workspaces"),
    "test_workspace_remote_contract.py": ("contract", "workspaces"),
    "test_workspace_retention.py": ("integration", "workspaces"),
    "test_workspace_sources.py": ("integration", "workspaces"),
    "test_workspace_task_management_composition.py": ("integration", "workspaces"),
    "test_workspaces.py": ("unit", "workspaces"),
}

DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("storage", ("storage", "garage", "rustfs", "object_store")),
    ("artifacts", ("artifact", "artifacts")),
    ("memory", ("memory",)),
    ("knowledge", ("knowledge",)),
    ("files", ("filesystem", "file_", "files_")),
    ("workspaces", ("workspace", "workspaces")),
    ("context", ("context", "source_", "sources_")),
    ("accounting", ("accounting", "usage_", "cost_")),
    ("notifications", ("notification", "inbox")),
    ("organizations", ("organization", "ownership", "subject_integrity")),
    (
        "application_distribution",
        (
            "application_distribution",
            "application_build",
            "remote_build",
            "build_worker",
            "placement",
        ),
    ),
    ("search", ("search",)),
    ("verification", ("verification", "verifier", "reviewer", "repair")),
    (
        "security",
        ("security", "authorization", "approval", "secret", "pipelock", "redaction"),
    ),
    (
        "models",
        ("model", "provider", "inference", "litellm", "openai", "sglang", "routing_profile"),
    ),
    (
        "distributed",
        (
            "distributed",
            "worker",
            "message_transport",
            "failover",
            "ha_",
            "node_reboot",
            "multi_process",
        ),
    ),
    ("deployment", ("deployment", "single_node", "server_")),
    (
        "recovery",
        (
            "backup",
            "restore",
            "recovery",
            "restart",
            "durable",
            "persistence",
            "migration",
            "replacement_machine",
        ),
    ),
    ("planning", ("planning", "plan_")),
    ("goals", ("goal",)),
    ("automation", ("automation",)),
    ("task_management", ("task_", "project_")),
    ("plugins", ("plugin", "marketplace", "registry")),
    ("skills", ("skill", "skillspector")),
    ("agents", ("agent", "team", "handoff")),
    ("connectors", ("connector",)),
    ("repository", ("repository", "git_")),
    ("evaluation", ("evaluation", "evidence", "rubric")),
    ("observability", ("observability", "telemetry", "trace")),
    ("control_plane", ("control_plane",)),
    ("forge", ("forge",)),
    ("browser", ("browser",)),
    ("cli", ("cli",)),
    ("portability", ("portability", "export", "import_")),
    ("configuration", ("configuration", "config")),
    ("release", ("release", "upgrade")),
    ("kernel", ("kernel",)),
    ("workflows", ("workflow",)),
)

SUITE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "performance",
        (
            "benchmark",
            "pressure",
            "performance",
            "scale",
            "latency",
            "throughput",
            "endurance",
            "stress",
            "sweep",
        ),
    ),
    ("e2e", ("_e2e", "end_to_end", "public_production")),
    (
        "contract",
        ("contract", "schema", "conformance", "protocol", "invariant", "manifest", "version_constraint"),
    ),
    (
        "release",
        ("release_maintenance", "upgrade_lifecycle", "upgrade_recovery", "canonical_runtime_assets"),
    ),
    (
        "regression",
        ("regression", "hardening", "final_", "completion", "review", "reopened", "reaudit", "gaps", "followup", "fixes"),
    ),
)


def all_modules() -> list[Path]:
    return sorted(p for p in TESTS.rglob("test_*.py") if p.is_file())


def is_candidate(path: Path) -> bool:
    return path.parent == TESTS or ISSUE_NAME.fullmatch(path.name) is not None


def clean_name(path: Path) -> str:
    if path.name in OTHER_HISTORICAL:
        return OTHER_HISTORICAL[path.name]
    match = ISSUE_NAME.fullmatch(path.name)
    if match:
        suffix = match.group("suffix").strip("_")
        return f"test_{suffix}.py"
    return path.name


def infer_domain(text: str) -> str:
    lowered = text.lower()
    for domain, tokens in DOMAIN_RULES:
        if any(token in lowered for token in tokens):
            return domain
    return "platform"


def infer_suite(path: Path, stem: str) -> str:
    relative = path.relative_to(TESTS)
    if len(relative.parts) > 1 and relative.parts[0] in CANONICAL_SUITES:
        return relative.parts[0]
    override = ROOT_OVERRIDES.get(path.name)
    if override:
        return override[0]
    lowered = stem.lower()
    for suite, tokens in SUITE_RULES:
        if any(token in lowered for token in tokens):
            return suite
    return "integration"


def existing_domain(path: Path) -> str | None:
    relative = path.relative_to(TESTS)
    if len(relative.parts) >= 3 and relative.parts[0] in CANONICAL_SUITES:
        return relative.parts[1]
    return None


def test_function_names(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            names.append(node.name)
    return names


def function_domain_counts(path: Path) -> Counter[str]:
    return Counter(
        domain
        for name in test_function_names(path)
        if (domain := infer_domain(name)) != "platform"
    )


def primary_function_domain(path: Path) -> str:
    counts = function_domain_counts(path)
    if not counts:
        return "platform"
    return counts.most_common(1)[0][0]


def mixed_domains(path: Path) -> list[str]:
    # A file with a clear domain in its own name is allowed to exercise cross-cutting
    # security/recovery/storage concerns. We only force a split when the historical
    # filename itself is generic and multiple responsibilities independently dominate
    # the contained test functions.
    if path.name in ROOT_OVERRIDES or existing_domain(path) is not None:
        return []
    cleaned_stem = PurePosixPath(clean_name(path)).stem
    if infer_domain(cleaned_stem) != "platform":
        return []
    counts = function_domain_counts(path)
    substantial = sorted(domain for domain, count in counts.items() if count >= 2)
    return substantial if len(substantial) >= 2 else []


def desired_destination(path: Path) -> Path:
    cleaned = clean_name(path)
    stem = PurePosixPath(cleaned).stem
    suite = infer_suite(path, stem)
    domain = existing_domain(path)
    if domain is None:
        override = ROOT_OVERRIDES.get(path.name)
        domain = override[1] if override else infer_domain(stem)
    if domain == "platform":
        domain = primary_function_domain(path)
    return TESTS / suite / domain / cleaned


def main() -> int:
    modules = all_modules()
    candidates = [p for p in modules if is_candidate(p)]
    candidate_set = set(candidates)
    planned: dict[Path, Path] = {}
    residual: dict[str, str] = {}
    mixed: dict[str, list[str]] = {}

    for path in candidates:
        domains = mixed_domains(path)
        if domains:
            mixed[path.as_posix()] = domains
            residual[path.as_posix()] = (
                "generic historical module has 2+ substantial test-function domains; split required"
            )
            continue
        planned[path] = desired_destination(path)

    groups: dict[Path, list[Path]] = defaultdict(list)
    for source, destination in planned.items():
        groups[destination].append(source)

    for destination, sources in groups.items():
        occupied = destination.exists() and destination not in candidate_set
        if len(sources) > 1 or occupied:
            reason = (
                f"destination collision at {destination.as_posix()}"
                if len(sources) > 1
                else f"destination already exists at {destination.as_posix()}"
            )
            for source in sources:
                residual[source.as_posix()] = reason
                planned.pop(source, None)

    suite_counts = Counter(destination.relative_to(TESTS).parts[0] for destination in planned.values())
    domain_counts = Counter(destination.relative_to(TESTS).parts[1] for destination in planned.values())
    report = {
        "baseline_module_count": len(modules),
        "candidate_count": len(candidates),
        "planned_move_count": len(planned),
        "residual_count": len(residual),
        "mixed_count": len(mixed),
        "suite_counts": dict(sorted(suite_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "mixed": mixed,
        "residual": residual,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if residual else 0


if __name__ == "__main__":
    raise SystemExit(main())
