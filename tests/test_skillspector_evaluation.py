from __future__ import annotations

from pathlib import Path

from experiments.skillspector.fixtures import FIXTURES, write_fixture_corpus
from experiments.skillspector.normalize import normalize_report
from experiments.skillspector.runner import container_command, sanitized_environment


VERSION = "2.11.2"
REVISION = "69dcdfb74487d361ba4c811d088cfdea2ff3a9dc"


def _normalize(report: dict[str, object], *, process_ok: bool = True):
    return normalize_report(
        report,
        provider_version=VERSION,
        provider_revision=REVISION,
        mode="static",
        candidate_digest="candidate-sha256",
        process_ok=process_ok,
    )


def _complete_report(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "execution_successful": True,
        "analysis_completeness": {
            "is_complete": True,
            "status": "complete",
            "coverage_percent": 100.0,
            "ledger_exceptions": [],
            "limitations": [],
        },
        "issues": [],
        "risk_assessment": {
            "score": 0,
            "severity": "LOW",
            "recommendation": "SAFE",
        },
        "suppressed_count": 0,
    }
    report.update(overrides)
    return report


def test_empty_completed_report_is_clean_advisory_evidence() -> None:
    evidence = _normalize(_complete_report())

    assert evidence.status == "clean"
    assert evidence.complete is True
    assert evidence.provider == "nvidia/skillspector"
    assert evidence.provider_revision == REVISION


def test_provider_issues_are_normalized_without_becoming_trust_state() -> None:
    evidence = _normalize(
        _complete_report(
            issues=[
                {
                    "finding_id": "finding-upstream-1",
                    "id": "P1",
                    "category": "prompt_injection",
                    "severity": "HIGH",
                    "confidence": 0.9,
                    "message": "Synthetic finding",
                    "location": {"file": "SKILL.md", "start_line": 4},
                }
            ],
            risk_assessment={
                "score": 91,
                "severity": "CRITICAL",
                "recommendation": "DO_NOT_INSTALL",
            },
        )
    )

    assert evidence.status == "findings"
    assert evidence.findings[0].severity == "high"
    assert evidence.findings[0].provider_id == "finding-upstream-1"
    assert evidence.findings[0].path == "SKILL.md"
    assert evidence.findings[0].line == 4
    assert evidence.provider_metadata["risk_assessment"] == {
        "score": 91,
        "severity": "CRITICAL",
        "recommendation": "DO_NOT_INSTALL",
    }
    assert not hasattr(evidence, "trust_state")
    assert not hasattr(evidence, "approved")


def test_provider_execution_failure_can_never_normalize_to_clean() -> None:
    evidence = _normalize(_complete_report(execution_successful=False))

    assert evidence.status == "degraded"
    assert evidence.complete is False
    assert "provider_execution_failed" in evidence.degraded_reasons


def test_scanner_process_failure_can_never_normalize_to_clean() -> None:
    evidence = _normalize(_complete_report(), process_ok=False)

    assert evidence.status == "degraded"
    assert evidence.complete is False
    assert "scanner_process_failed" in evidence.degraded_reasons


def test_partial_provider_report_can_never_normalize_to_clean() -> None:
    evidence = _normalize(
        _complete_report(
            analysis_completeness={
                "is_complete": False,
                "status": "partial",
                "coverage_percent": 75.0,
                "ledger_exceptions": [{"reason_code": "read_error"}],
            }
        )
    )

    assert evidence.status == "degraded"
    assert evidence.complete is False
    assert "provider_analysis_incomplete" in evidence.degraded_reasons
    assert "provider_analysis_status=partial" in evidence.degraded_reasons


def test_high_risk_recommendation_remains_metadata_not_failure() -> None:
    evidence = _normalize(
        _complete_report(
            risk_assessment={
                "score": 100,
                "severity": "CRITICAL",
                "recommendation": "DO_NOT_INSTALL",
            }
        )
    )

    assert evidence.complete is True
    assert evidence.status == "clean"
    assert evidence.provider_metadata["risk_assessment"]["recommendation"] == "DO_NOT_INSTALL"


def test_container_command_enforces_baseline_isolation(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    command = container_command("docker", "skillspector-eval:2.11.2", input_dir, output_dir)

    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert f"{input_dir}:/scan:ro" in command
    assert "--no-llm" in command


def test_environment_allowlist_excludes_common_secret_names(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")

    environment = sanitized_environment()

    assert environment["PATH"] == "/bin"
    assert "OPENAI_API_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment


def test_fixture_corpus_is_deterministic_and_contains_benign_control(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    write_fixture_corpus(first)
    write_fixture_corpus(second)

    assert "benign/SKILL.md" in FIXTURES
    assert set(path.relative_to(first) for path in first.rglob("SKILL.md")) == set(
        path.relative_to(second) for path in second.rglob("SKILL.md")
    )
    for relative, expected in FIXTURES.items():
        assert (first / relative).read_text(encoding="utf-8") == expected
        assert (second / relative).read_text(encoding="utf-8") == expected
