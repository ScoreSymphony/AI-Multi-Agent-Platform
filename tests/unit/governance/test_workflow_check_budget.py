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


def test_duplicate_post_merge_validation_stays_off_main_push() -> None:
    assert "\n  push:" not in _trigger_block("codeql.yml")
    assert "\n  push:" not in _trigger_block("conformance.yml")

    repository_quality_triggers = _trigger_block("repository-quality.yml")
    assert "main" not in repository_quality_triggers


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
