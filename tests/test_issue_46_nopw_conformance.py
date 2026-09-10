from __future__ import annotations

from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    activate_optional_scenarios,
    profile_scenarios,
)


def _by_id():
    return {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }


def _command(scenario_id: str) -> str:
    scenario = _by_id()[scenario_id]
    return " ".join(scenario.command or ())


def test_notification_claim_binds_task_approval_verification_scope_and_dedupe_evidence() -> None:
    scenarios = {
        scenario.scenario_id: scenario
        for scenario in activate_optional_scenarios(ConformanceProfile.RELEASE, ("N",))
    }
    notification = scenarios["N"]
    command = " ".join(notification.command or ())

    assert notification.required is True
    assert "completion/failure" in notification.criterion
    assert "approval-required" in notification.criterion
    assert "verification-required" in notification.criterion
    for node in (
        "test_control_plane_inbox_is_recipient_scoped_and_commands_are_idempotent",
        "test_authenticated_http_ignores_spoofed_owner_headers_for_notification_inbox",
        "test_task_completed_and_failed_events_project_to_canonical_notifications",
        "test_duplicate_task_event_aggregates_without_notification_storm",
        "test_authorization_gate_required_and_resolved_events_project_into_notifications",
        "test_approval_required_projection_uses_exact_approval_reference_without_payload",
        "test_verification_required_and_changes_requested_use_opaque_issue86_attention_contract",
        "test_event_provider_projects_task_event_and_replay_aggregates_safely",
    ):
        assert node in command


def test_usage_claim_binds_task_model_worker_node_and_unavailable_measurement_evidence() -> None:
    scenario = _by_id()["O"]
    command = _command("O")

    assert scenario.required is True
    assert "Task/model/Worker/Node" in scenario.criterion
    assert "without fabricating unavailable measurements" in scenario.criterion
    for node in (
        "test_task_run_executor_accounting_is_idempotent_and_aggregated",
        "test_auto_routed_model_usage_is_attributed_to_selected_canonical_configuration",
        "test_worker_dispatch_usage_is_additive_and_attributed",
        "test_worker_and_node_reported_resources_are_latest_provider_neutral_gauges",
        "test_missing_measurement_is_unavailable_not_zero",
    ):
        assert node in command


def test_standard_definition_claim_binds_discovery_customization_and_independent_removal() -> None:
    scenario = _by_id()["P"]
    command = _command("P")

    assert scenario.required is True
    assert "discoverable configuration" in scenario.criterion
    assert "customizable" in scenario.criterion
    assert "independently removable" in scenario.criterion
    for node in (
        "test_standard_catalog_is_discoverable_without_installing_definitions",
        "test_standard_catalog_lifecycle_uses_real_control_plane_http_command_path",
        "test_control_plane_bootstrap_clone_scope_customize_and_delete_workflow",
        "test_scoped_software_team_clone_requires_explicit_scope_and_is_deletable",
    ):
        assert node in command


def test_task_management_claim_binds_authorization_and_worker_admission_evidence() -> None:
    scenario = _by_id()["W"]
    command = _command("W")

    assert scenario.required is True
    assert "authorization" in scenario.criterion
    assert "Worker admission" in scenario.criterion
    assert "test_bulk_update_preflights_per_task_authorization" in command
    assert "test_urgent_task_cannot_bypass_distributed_worker_admission" in command
