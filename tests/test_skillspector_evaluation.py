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


def test_empty_completed_report_is_clean_advisory_evidence() -> None:
    evidence = _normalize({"findings": [], "analysis_completeness": "complete"})

    assert evidence.status == "clean"
    assert evidence.complete is True
    assert evidence.provider == "nvidia/skillspector"
    assert evidence.provider_revision == REVISION


def test_provider_findings_are_normalized_without_becoming_trust_state() -> None:
    evidence = _normalize(
        {
            "findings": [
                {
                    "id": "UPSTREAM-1",
                    "category": "prompt_injection",
                    "severity": "HIGH",
                    "confidence": 0.9,
                    "title": "Synthetic finding",
                    "location": {"path": "SKILL.md", "line": 4},
                }
            ],
            "safe_to_install": False,
            "risk_score": 91,
        }
    )

    assert evidence.status == "findings"
    assert evidence.findings[0].severity == "high"
    assert evidence.findings[0].provider_id == "UPSTREAM-1"
    assert evidence.provider_metadata["safe_to_install"] is False
    assert not hasattr(evidence, "trust_state")
    assert not hasattr(evidence, "approved")


def test_scanner_failure_can_never_normalize_to_clean() -> None:
    evidence = _normalize({"findings": []}, process_ok=False)

    assert evidence.status == "degraded"
    assert evidence.complete is False
    assert "scanner_process_failed" in evidence.degraded_reasons


def test_partial_provider_report_can_never_normalize_to_clean() -> None:
    evidence = _normalize({"findings": [], "analysis_completeness": "partial"})

    assert evidence.status == "degraded"
    assert evidence.complete is False
    assert "provider_analysis_completeness=partial" in evidence.degraded_reasons


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
