from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_fast_profile_binds_d_vertical_to_authenticated_local_capability_evidence() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.FAST)
    }

    scenario = scenarios["D-vertical"]
    assert scenario.required is True
    assert scenario.command is not None
    command = " ".join(scenario.command)
    assert "tests/test_issue_46_local_model_native_capability_e2e.py" in command
    assert "test_authenticated_local_model_executes_native_capability_end_to_end" in command

    # Keep the lower-level diagnostics as separate required evidence instead of hiding
    # model or capability regressions behind the combined vertical.
    assert scenarios["D-model"].required is True
    assert scenarios["D-capability"].required is True
