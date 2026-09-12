from __future__ import annotations

import json
import sys
from pathlib import Path

from ai_multi_agent_platform.conformance import (
    CompatibilityResult,
    ConformanceProfile,
    ConformanceScenario,
    ConformanceStatus,
    profile_scenarios,
    run_conformance,
)
from ai_multi_agent_platform.conformance.evidence import EVIDENCE_PREFIX


def _emitting_command(payload: object) -> tuple[str, ...]:
    encoded = json.dumps(payload)
    statement = (
        "import json; "
        f"print({EVIDENCE_PREFIX!r} + json.dumps(json.loads({encoded!r}), sort_keys=True))"
    )
    return (sys.executable, "-c", statement)


def test_runtime_evidence_is_promoted_into_scenario_report(tmp_path: Path) -> None:
    scenario = ConformanceScenario(
        scenario_id="runtime-evidence",
        owner="#46 runtime evidence",
        criterion="canonical IDs and evidence are retained",
        command=_emitting_command(
            {
                "canonical_resource_ids": ["task_demo", "run_demo", "artifact_demo"],
                "evidence": ["api:/api/v1/tasks/task_demo", "artifact:artifact_demo"],
            }
        ),
        requires_runtime_evidence=True,
    )

    report = run_conformance(
        ConformanceProfile.RELEASE,
        repository_root=tmp_path,
        scenarios=(scenario,),
    )
    result = report.scenarios[0]

    assert report.passed is True
    assert report.compatibility_result == CompatibilityResult.COMPATIBLE.value
    assert result.status == ConformanceStatus.PASS.value
    assert result.canonical_resource_ids == ("task_demo", "run_demo", "artifact_demo")
    assert result.evidence == (
        "registered-command",
        "api:/api/v1/tasks/task_demo",
        "artifact:artifact_demo",
    )


def test_required_runtime_evidence_is_fail_closed_when_missing(tmp_path: Path) -> None:
    scenario = ConformanceScenario(
        scenario_id="missing-evidence",
        owner="#46 runtime evidence",
        criterion="evidence-bearing claim cannot silently pass",
        command=(sys.executable, "-c", "print('scenario passed without evidence')"),
        requires_runtime_evidence=True,
    )

    report = run_conformance(
        ConformanceProfile.RELEASE,
        repository_root=tmp_path,
        scenarios=(scenario,),
    )
    result = report.scenarios[0]

    assert report.passed is False
    assert report.compatibility_result == CompatibilityResult.INCOMPATIBLE.value
    assert result.status == ConformanceStatus.FAIL.value
    assert result.failure_category == "runtime_evidence_missing"
    assert result.canonical_resource_ids == ()
    assert result.evidence == ("registered-command",)


def test_malformed_runtime_evidence_is_fail_closed(tmp_path: Path) -> None:
    scenario = ConformanceScenario(
        scenario_id="invalid-evidence",
        owner="#46 runtime evidence",
        criterion="malformed evidence cannot support compatibility",
        command=(sys.executable, "-c", f"print({EVIDENCE_PREFIX!r} + '{{not-json}}')"),
    )

    report = run_conformance(
        ConformanceProfile.RELEASE,
        repository_root=tmp_path,
        scenarios=(scenario,),
    )
    result = report.scenarios[0]

    assert report.passed is False
    assert result.status == ConformanceStatus.FAIL.value
    assert result.failure_category == "runtime_evidence_invalid"


def test_release_vertical_requires_runtime_evidence_and_disables_pytest_capture() -> None:
    scenario = next(
        scenario
        for scenario in profile_scenarios(ConformanceProfile.RELEASE)
        if scenario.scenario_id == "REL-VERTICAL"
    )

    assert scenario.requires_runtime_evidence is True
    assert scenario.command is not None
    assert "-s" in scenario.command
