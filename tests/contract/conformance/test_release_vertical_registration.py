from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_authenticated_full_vertical_slice_is_required_release_evidence() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }

    scenario = scenarios["REL-VERTICAL"]
    assert scenario.required is True
    assert scenario.command is not None

    command = " ".join(scenario.command)
    assert "tests/test_issue_46_worker_artifact_verification_vertical.py" in command
    assert "test_authenticated_worker_artifact_is_exact_verification_evidence_same_run" in command
    assert "Worker/Node" in scenario.criterion
    assert "Verification" in scenario.criterion
    assert "API/timeline/observability" in scenario.criterion
