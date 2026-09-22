import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _trigger_block(name: str) -> str:
    text = _text(name)
    marker = "\npermissions:"
    if marker in text:
        return text.split(marker, 1)[0]
    return text.split("\njobs:", 1)[0]


def _job_ids(name: str) -> set[str]:
    jobs = _text(name).split("\njobs:\n", 1)[1]
    return set(re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", jobs, flags=re.MULTILINE))


def test_core_ci_and_optional_compatibility_are_split() -> None:
    assert _job_ids("ci.yml") == {
        "python-validation",
        "test",
        "single-node-install-smoke",
        "frontend",
    }
    assert _job_ids("compatibility.yml") == {
        "litellm-compat",
        "bifrost-pinned-compat",
        "hermes-pinned-compat",
    }

    compatibility_triggers = _trigger_block("compatibility.yml")
    assert "pull_request:" in compatibility_triggers
    assert "\n  push:" not in compatibility_triggers


def test_pr_governance_only_emits_dependency_review() -> None:
    assert _job_ids("governance.yml") == {"dependency-review"}
    governance_triggers = _trigger_block("governance.yml")
    assert "pull_request:" in governance_triggers
    assert "issues:" not in governance_triggers
    assert "schedule:" not in governance_triggers

    maintenance_triggers = _trigger_block("governance-maintenance.yml")
    assert "pull_request:" not in maintenance_triggers
    assert "issues:" in maintenance_triggers
    assert "schedule:" in maintenance_triggers
    assert "workflow_dispatch:" in maintenance_triggers
    assert _job_ids("governance-maintenance.yml") == {"validate", "discover"}


def test_codeql_and_repository_quality_recheck_main() -> None:
    codeql_triggers = _trigger_block("codeql.yml")
    assert "\n  push:" in codeql_triggers
    assert "main" in codeql_triggers
    assert "workflow_dispatch:" in codeql_triggers

    assert "\n  push:" not in _trigger_block("conformance.yml")

    repository_quality_triggers = _trigger_block("repository-quality.yml")
    assert "\n  push:" in repository_quality_triggers
    assert "main" in repository_quality_triggers


def test_routine_main_push_budget_is_ten_checks() -> None:
    routine_push_workflows = (
        "ci.yml",
        "benchmark-smoke.yml",
        "performance-extended-smoke.yml",
    )
    for workflow in routine_push_workflows:
        triggers = _trigger_block(workflow)
        assert "\n  push:" in triggers
        assert "main" in triggers

    routine_checks = sum(len(_job_ids(workflow)) for workflow in routine_push_workflows)
    assert routine_checks == 10
    assert routine_checks <= 10


def test_repository_maintenance_stays_out_of_pr_gate_and_cleans_orphans() -> None:
    maintenance_triggers = _trigger_block("repository-maintenance.yml")
    assert "workflow_dispatch:" in maintenance_triggers
    assert "schedule:" in maintenance_triggers
    assert "\n  push:" in maintenance_triggers
    assert "main" in maintenance_triggers
    assert '".github/workflows/**"' in maintenance_triggers
    assert "pull_request:" not in maintenance_triggers

    maintenance = _text("repository-maintenance.yml")
    assert "cleanup-orphaned-actions-history" not in _text("repository-quality.yml")
    assert "cleanup-orphaned-actions-history" in maintenance
    assert "full_history_scan:" in maintenance
    assert "listRepoWorkflows" in maintenance
    assert "workflow.state !== 'deleted'" in maintenance
    assert "status: 'completed'" in maintenance
    assert "perPage = 100" in maintenance
    assert "routinePageBudget = 10" in maintenance
    assert "listWorkflowRuns" in maintenance
    assert "listWorkflowRunsForRepo" in maintenance
    assert "currentPaths.has(run.path)" in maintenance
    assert "maxDelete = 4000" in maintenance


def test_conformance_keeps_pr_and_scheduled_coverage() -> None:
    triggers = _trigger_block("conformance.yml")
    assert "pull_request:" in triggers
    assert "schedule:" in triggers
    assert "workflow_dispatch:" in triggers

    conformance = _text("conformance.yml")
    assert "github.event_name == 'schedule'" in conformance
    assert "github.event_name == 'pull_request'" in conformance
