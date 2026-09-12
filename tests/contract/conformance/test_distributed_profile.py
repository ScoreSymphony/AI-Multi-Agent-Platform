from __future__ import annotations

from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    activate_optional_scenarios,
)


def test_distributed_profile_binds_complete_scenario_e_evidence() -> None:
    scenario = next(
        scenario
        for scenario in activate_optional_scenarios(ConformanceProfile.RELEASE, ("E",))
        if scenario.scenario_id == "E"
    )

    assert scenario.required is True
    assert scenario.command is not None
    command = " ".join(scenario.command)

    assert "test_two_node_selection_filters_resources_capabilities_and_model" in command
    assert (
        "test_dispatch_authorization_denial_releases_reservation_before_worker_execution" in command
    )
    assert "test_remote_worker_file_becomes_canonical_run_artifact" in command
    assert (
        "test_terminal_result_is_recovered_after_restart_and_then_survives_without_worker"
        in command
    )
    assert "test_scheduler_reservation_and_dispatch_emit_correlated_safe_telemetry" in command
