from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_reference_multi_agent_conformance_reuses_maintained_golden_path() -> None:
    scenario = next(
        item for item in profile_scenarios(ConformanceProfile.FAST) if item.scenario_id == "MA"
    )

    assert scenario.required is True
    assert scenario.command is not None
    assert scenario.owner == "reference multi-agent baseline"
    command = " ".join(scenario.command)
    assert "tests/integration/deployment/test_reference_multi_agent_provenance.py" in command
    assert (
        "test_reference_multi_agent_golden_path_persists_complete_canonical_provenance" in command
    )
