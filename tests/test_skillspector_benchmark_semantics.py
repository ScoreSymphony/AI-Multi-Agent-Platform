from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.skillspector.fixtures import FIXTURES  # noqa: E402
from experiments.skillspector.normalize import normalize_report  # noqa: E402
from experiments.skillspector.run_benchmark import _finding_signature  # noqa: E402


def _normalize(issue: dict[str, object]):
    return normalize_report(
        {
            "skill": {"name": "fixture", "source": "/scan"},
            "execution_successful": True,
            "analysis_completeness": {"is_complete": True, "status": "complete"},
            "issues": [issue],
            "risk_assessment": {"score": 40, "severity": "MEDIUM", "recommendation": "CAUTION"},
        },
        provider_version="2.11.2",
        provider_revision="69dcdfb74487d361ba4c811d088cfdea2ff3a9dc",
        mode="static_no_llm_network_none",
        policy_config_version="test-policy-v1",
        candidate_id="fixture",
        candidate_revision="generated-corpus-v3",
        candidate_digest="candidate-digest",
        network_usage={"network_allowed": False, "services": []},
        provider_usage={"llm_assisted": False, "provider": None},
        observed_at="2026-09-12T00:00:00+00:00",
    )


def test_normalizer_preserves_occurrence_id_and_stable_rule_id_separately() -> None:
    evidence = _normalize(
        {
            "finding_id": "random-occurrence-uuid",
            "id": "P1",
            "category": "Prompt Injection",
            "severity": "MEDIUM",
            "confidence": 0.8,
            "finding": "ignore previous instructions",
            "file": "SKILL.md",
            "line": 7,
        }
    )

    finding = evidence.findings[0]
    assert finding.provider_id == "random-occurrence-uuid"
    assert finding.rule_id == "P1"
    assert finding.summary == "ignore previous instructions"


def test_semantic_signature_ignores_random_occurrence_uuid() -> None:
    first = _normalize(
        {
            "finding_id": "uuid-run-1",
            "id": "P1",
            "category": "Prompt Injection",
            "severity": "MEDIUM",
            "confidence": 0.8,
            "finding": "ignore previous instructions",
            "file": "SKILL.md",
            "line": 7,
        }
    ).to_dict()
    second = _normalize(
        {
            "finding_id": "uuid-run-2",
            "id": "P1",
            "category": "Prompt Injection",
            "severity": "MEDIUM",
            "confidence": 0.8,
            "finding": "ignore previous instructions",
            "file": "SKILL.md",
            "line": 7,
        }
    ).to_dict()

    assert _finding_signature(first) == _finding_signature(second)


def test_semantic_signature_detects_real_rule_change() -> None:
    first = _normalize(
        {
            "finding_id": "uuid-run-1",
            "id": "P1",
            "category": "Prompt Injection",
            "severity": "MEDIUM",
            "confidence": 0.8,
            "finding": "ignore previous instructions",
            "file": "SKILL.md",
            "line": 7,
        }
    ).to_dict()
    second = _normalize(
        {
            "finding_id": "uuid-run-2",
            "id": "P2",
            "category": "Prompt Injection",
            "severity": "MEDIUM",
            "confidence": 0.8,
            "finding": "ignore previous instructions",
            "file": "SKILL.md",
            "line": 7,
        }
    ).to_dict()

    assert _finding_signature(first) != _finding_signature(second)


def test_corpus_covers_benign_network_and_exfiltration_source_sink_classes() -> None:
    assert "benign-legitimate-network/SKILL.md" in FIXTURES
    network_code = FIXTURES["benign-legitimate-network/fixture.py"]
    assert "requests.get" in network_code

    exfiltration_code = FIXTURES["exfiltration/fixture.py"]
    for marker in (
        "os.environ.get",
        '".ssh"',
        'Path(".env")',
        "requests.post",
        "requests.get",
    ):
        assert marker in exfiltration_code


def test_corpus_covers_dynamic_execution_privilege_persistence_and_destructive_patterns() -> None:
    dangerous_code = FIXTURES["dangerous-code/fixture.py"]
    for marker in (
        "subprocess.run",
        "eval(",
        "os.system",
        "sudo",
        "autostart",
        "shutil.rmtree",
    ):
        assert marker in dangerous_code
