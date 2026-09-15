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
    assert _job_ids("ci.yml") == {"test", "single-node-install-smoke", "frontend"}
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


def test_duplicate_post_merge_validation_stays_off_main_push() -> None:
    assert "\n  push:" not in _trigger_block("codeql.yml")
    assert "\n  push:" not in _trigger_block("conformance.yml")

    repository_quality_triggers = _trigger_block("repository-quality.yml")
    assert "main" not in repository_quality_triggers


def test_routine_main_push_budget_is_nine_checks() -> None:
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
    assert routine_checks == 9
    assert routine_checks <= 10


def test_repository_maintenance_is_manual_only() -> None:
    maintenance_triggers = _trigger_block("repository-maintenance.yml")
    assert "workflow_dispatch:" in maintenance_triggers
    assert "pull_request:" not in maintenance_triggers
    assert "\n  push:" not in maintenance_triggers

    assert "cleanup-orphaned-actions-history" not in _text("repository-quality.yml")
    assert "cleanup-orphaned-actions-history" in _text("repository-maintenance.yml")


def test_conformance_keeps_pr_and_scheduled_coverage() -> None:
    triggers = _trigger_block("conformance.yml")
    assert "pull_request:" in triggers
    assert "schedule:" in triggers
    assert "workflow_dispatch:" in triggers

    conformance = _text("conformance.yml")
    assert "github.event_name == 'schedule'" in conformance
    assert "github.event_name == 'pull_request'" in conformance
