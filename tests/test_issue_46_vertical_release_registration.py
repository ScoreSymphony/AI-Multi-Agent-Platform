from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_authenticated_reference_vertical_slice_is_required_release_evidence() -> None:
    scenarios = {
        scenario.scenario_id: scenario
        for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }

    scenario = scenarios["REL-VERTICAL"]
    assert scenario.required is True
    assert scenario.command is not None

    command = " ".join(scenario.command)
    assert "tests/test_issue_46_reference_vertical_slice.py" in command
    assert (
        "test_authenticated_reference_vertical_preserves_canonical_evidence_end_to_end"
        in command
    )
