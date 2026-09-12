from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_runtime_verification_scenario_keeps_required_acceptance_evidence_explicit() -> None:
    scenario = next(
        scenario
        for scenario in profile_scenarios(ConformanceProfile.FAST)
        if scenario.scenario_id == "U"
    )

    assert scenario.required is True
    assert scenario.command is not None
    command = " ".join(scenario.command)

    assert "test_successful_run_cannot_bypass_required_verification" in command
    assert "test_changed_subject_invalidates_old_verification_at_completion_gate" in command
    assert "test_rejected_verification_blocks_completion_without_rewriting_run_outcome" in command
    assert "test_changed_result_revision_cannot_reuse_old_verification" in command
    assert "test_deterministic_reference_verifier_passes_and_fails_without_llm" in command
    assert "test_agent_reviewer_independence_and_read_only_rules_are_enforced" in command
    assert "test_bounded_repair_preserves_history_and_stops_at_policy_limit" in command

    assert "revisions" in scenario.criterion
    assert "without an LLM" in scenario.criterion
    assert "reviewer independence" in scenario.criterion
    assert "bounded and auditable" in scenario.criterion
