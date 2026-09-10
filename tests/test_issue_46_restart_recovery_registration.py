from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_scenario_h_targets_unfinished_run_recovery_without_duplicate_dispatch() -> None:
    scenario = next(
        item for item in profile_scenarios(ConformanceProfile.FAST) if item.scenario_id == "H"
    )
    command = " ".join(scenario.command or ())

    assert scenario.required is True
    assert "test_restart_reconciles_post_accept_crash_without_duplicate_dispatch" in command
    assert "test_recovery_distinguishes_queued_pre_accept_and_orphaned_running" in command
    assert "test_restart_between_run_creation_and_binding_recovers_same_run" in command
    assert "ai_multi_agent_platform.cli.acceptance" not in command
