"""Explicit activation of optional #46 compatibility profiles.

Optional scenarios remain non-claims by default. Once a caller explicitly enables one,
that scenario becomes required for the selected deployment claim and must execute its
maintained acceptance evidence. Optional scenarios without registered evidence become
required ``not_implemented`` results rather than silently passing.
"""

from __future__ import annotations

import sys
from dataclasses import replace

from .gate import (
    ConformanceProfile,
    ConformanceScenario,
    ConformanceStatus,
    profile_scenarios,
)


def _pytest(*nodes: str) -> tuple[str, ...]:
    return (sys.executable, "-m", "pytest", "-q", *nodes)


def _external(profile_id: str) -> tuple[str, ...]:
    return (
        sys.executable,
        "scripts/ci/issue46_external_profile.py",
        profile_id,
    )


_OPTIONAL_EVIDENCE: dict[str, tuple[str, ...]] = {
    "B": _external("B"),
    "C": _external("C"),
    "E": _pytest(
        "tests/integration/distributed/test_distributed_runtime.py::"
        "test_two_node_selection_filters_resources_capabilities_and_model",
        "tests/integration/security/test_security_result_recovery.py::"
        "test_dispatch_authorization_denial_releases_reservation_before_worker_execution",
        "tests/integration/artifacts/test_worker_artifact_integration.py::"
        "test_remote_worker_file_becomes_canonical_run_artifact",
        "tests/integration/security/test_security_result_recovery.py::"
        "test_terminal_result_is_recovered_after_restart_and_then_survives_without_worker",
        "tests/integration/distributed/test_distributed_telemetry.py::"
        "test_scheduler_reservation_and_dispatch_emit_correlated_safe_telemetry",
    ),
    "N": _pytest(
        "tests/integration/notifications/test_control_plane_inbox.py::"
        "test_control_plane_inbox_is_recipient_scoped_and_commands_are_idempotent",
        "tests/integration/notifications/test_control_plane_inbox.py::"
        "test_authenticated_http_ignores_spoofed_owner_headers_for_notification_inbox",
        "tests/unit/notifications/test_notification_service.py::"
        "test_task_completed_and_failed_events_project_to_canonical_notifications",
        "tests/unit/notifications/test_notification_service.py::"
        "test_duplicate_task_event_aggregates_without_notification_storm",
        "tests/integration/context/test_completed_source_runtime.py::"
        "test_authorization_gate_required_and_resolved_events_project_into_notifications",
        "tests/unit/notifications/test_notification_service.py::"
        "test_approval_required_projection_uses_exact_approval_reference_without_payload",
        "tests/regression/notifications/test_followup_integrations.py::"
        "test_verification_required_and_changes_requested_use_opaque_issue86_attention_contract",
        "tests/unit/notifications/test_event_projection.py::"
        "test_event_provider_projects_task_event_and_replay_aggregates_safely",
    ),
    "Q": _pytest(
        "tests/regression/security/test_reopened_p0_authorization.py::"
        "test_reapply_authorizes_instance_and_exact_source_revision",
        "tests/regression/security/test_reopened_p0_authorization.py::"
        "test_unauthorized_dependency_blocks_preview_and_apply_before_resource_creation",
        "tests/regression/platform/test_reopened_compatibility.py::"
        "test_version_incompatibilities_appear_in_preview_and_block_apply",
        "tests/regression/security/test_reopened_materialized_secret_guard.py::"
        "test_configuration_reference_plaintext_secret_is_rejected_before_handler",
        "tests/integration/repository/test_capability_assignment_compensation.py::"
        "test_composite_failure_compensates_earlier_capability_assignment",
        "tests/regression/models/test_final_composition.py::"
        "test_standard_single_node_exposes_final_template_integrations",
        "tests/integration/templates/test_revision_stability.py::"
        "test_composite_reapply_keeps_original_revision_until_explicit_upgrade",
    ),
    "R": _pytest(
        "tests/integration/control_plane/test_control_plane_portability.py::"
        "test_control_plane_binds_export_preview_and_import_without_client_owned_plan",
        "tests/integration/cli/test_cli_portability.py::"
        "test_portability_cli_exports_and_previews_through_canonical_commands",
        "tests/integration/cli/test_cli_portability.py::"
        "test_portability_cli_requires_confirmation_and_never_submits_import_plan",
        "tests/integration/files/test_file_portability.py::"
        "test_file_artifact_package_round_trip_remaps_provider_and_ids",
        "tests/integration/files/test_file_portability.py::"
        "test_file_snapshot_rejects_bytes_that_do_not_match_canonical_checksum",
        "tests/integration/agents/test_agent_portability.py::"
        "test_preview_reports_existing_id_conflict_before_any_mutation",
        "tests/integration/portability/test_portability.py::test_portable_package_round_trip_preserves_manifest_and_integrity",
        "tests/integration/portability/test_portability.py::test_plaintext_secret_bearing_field_is_rejected",
        "tests/integration/portability/test_portability.py::test_backend_private_runtime_state_is_rejected_recursively",
        "tests/integration/portability/test_import_executor.py::"
        "test_executor_imports_agent_then_team_with_full_revision_history",
        "tests/integration/portability/test_import_executor.py::"
        "test_executor_rolls_back_real_team_and_agent_in_reverse_order",
    ),
    "S": _pytest(
        "tests/integration/deployment/test_production_composition.py::"
        "test_default_single_node_keeps_registry_and_plugin_runtime_absent_when_unconfigured",
        "tests/integration/deployment/test_production_composition.py::"
        "test_configured_single_node_shares_registry_plugins_with_canonical_plugin_lifecycle",
        "tests/integration/plugins/test_registry_control_plane.py::"
        "test_preview_uses_server_resolved_validation_context_without_activation_router",
        "tests/regression/plugins/test_marketplace_completion.py::"
        "test_signed_artifact_requires_and_accepts_authoritative_verification",
    ),
    "T": _pytest(
        "tests/integration/control_plane/test_repository_run_provenance.py::"
        "test_control_plane_records_repository_input_before_start_and_on_retry",
        "tests/integration/workspaces/test_repository_run_changes.py::"
        "test_repository_run_records_exact_input_and_returns_changed_file_artifacts",
        "tests/integration/workspaces/test_repository_run_input_revision.py::"
        "test_run_input_recovers_materialized_sha_when_snapshot_keeps_symbolic_ref",
    ),
    "V": _pytest(
        "tests/integration/organizations/test_organization_domain.py::"
        "test_personal_scope_does_not_require_an_organization",
        "tests/integration/organizations/test_organization_domain.py::"
        "test_suspend_remove_and_role_changes_feed_scope_without_becoming_authorization",
        "tests/integration/organizations/test_organization_domain.py::"
        "test_resource_ownership_sharing_revoke_and_cross_org_isolation",
        "tests/integration/security/test_cross_organization_authorization.py::"
        "test_cross_org_share_flag_is_not_a_substitute_for_authorization",
        "tests/integration/task_management/test_historical_provenance.py::"
        "test_historical_task_and_event_identity_survive_membership_removal",
    ),
    "X": _pytest(
        "tests/integration/control_plane/test_control_plane_ha.py::"
        "test_active_passive_promotion_fences_stale_old_leader",
        "tests/integration/distributed/test_failover_reconciliation.py::"
        "test_restart_promotion_reconciles_running_work_and_preserves_worker_identity",
        "tests/regression/distributed/test_final_failover_acceptance.py::"
        "test_duplicate_command_replay_after_promotion_does_not_duplicate_task_or_run",
    ),
    "Y": _pytest(
        "tests/integration/recovery/test_coordination_durability.py::"
        "test_crash_after_run_creation_before_coordinator_commit_is_exactly_once",
        "tests/integration/recovery/test_coordination_durability.py::"
        "test_sqlite_deadline_wait_survives_restart_and_resumes_once",
        "tests/integration/recovery/test_coordination_durability.py::"
        "test_sqlite_retry_deadline_survives_restart_and_fires_once",
        "tests/integration/recovery/test_coordination_durability.py::"
        "test_sqlite_partial_fan_in_survives_restart",
        "tests/integration/recovery/test_coordination_durability.py::"
        "test_sqlite_stale_fence_cannot_commit_after_takeover",
        "tests/integration/knowledge/test_lost_worker_acknowledgement.py::"
        "test_lost_worker_acknowledgement_delegates_to_kernel_without_blind_redispatch",
        "tests/integration/recovery/test_restore_history_consistency.py::"
        "test_restore_preserves_kernel_history_and_resumes_wait_and_retry_once",
        "tests/integration/observability/test_observability_completeness.py::"
        "test_attempt_barrier_and_cancellation_evidence_is_explicit",
        "tests/integration/observability/test_observability_completeness.py::"
        "test_claim_conflicts_are_emitted_from_the_shared_claim_boundary",
        "tests/integration/distributed/test_distributed_worker_integration.py::"
        "test_lost_worker_ack_reconciles_through_real_distributed_worker_without_redispatch",
        "tests/integration/distributed/test_distributed_worker_integration.py::"
        "test_plan_cancellation_reaches_worker_and_late_worker_success_cannot_revive_state",
        "tests/integration/application_distribution/test_orchestrator_replacement.py::"
        "test_orchestrator_replacement_does_not_change_durable_step_identity_or_state",
        "tests/integration/security/test_coordination_control_plane_authorization.py::"
        "test_coordination_repair_commands_are_authorized_before_handler_execution",
        "tests/integration/cli/test_reference_coordinator_cli.py::"
        "test_cli_reads_real_reference_coordinator_projection_through_control_plane",
        "tests/integration/workflows/test_workflows_workflow_progress.py",
        "tests/unit/cli/test_workflow_progress.py",
        "tests/integration/verification/test_operator_repair.py::"
        "test_operator_can_cancel_only_an_explicit_missing_run_inconsistency_idempotently",
        "tests/integration/verification/test_operator_repair.py::"
        "test_operator_repair_refuses_to_cancel_when_the_canonical_run_exists",
    ),
}


def optional_evidence_ids() -> tuple[str, ...]:
    """Return optional scenario IDs with maintained executable #46 evidence."""

    return tuple(sorted(_OPTIONAL_EVIDENCE))


def activate_optional_scenarios(
    profile: ConformanceProfile,
    enabled_optional: tuple[str, ...] | list[str] | set[str] = (),
) -> tuple[ConformanceScenario, ...]:
    """Return one profile with explicitly enabled optional scenarios made claim-blocking."""

    scenarios = profile_scenarios(profile)
    enabled = frozenset(value.strip().upper() for value in enabled_optional if value.strip())
    by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    unknown = enabled - by_id.keys()
    if unknown:
        available = ", ".join(sorted(by_id))
        requested = ", ".join(sorted(unknown))
        raise ValueError(
            f"optional scenario(s) not present in {profile.value} profile: {requested}; "
            f"available scenario IDs: {available}"
        )

    selected: list[ConformanceScenario] = []
    for scenario in scenarios:
        if scenario.scenario_id not in enabled:
            selected.append(scenario)
            continue
        if scenario.required:
            raise ValueError(
                f"scenario {scenario.scenario_id} is already required by the "
                f"{profile.value} profile"
            )

        command = _OPTIONAL_EVIDENCE.get(scenario.scenario_id)
        if command is None:
            selected.append(
                replace(
                    scenario,
                    required=True,
                    unavailable_status=ConformanceStatus.NOT_IMPLEMENTED,
                    unavailable_reason=(
                        "the optional profile was explicitly enabled, but no maintained #46 "
                        "acceptance command is registered for this compatibility claim"
                    ),
                )
            )
            continue

        selected.append(
            replace(
                scenario,
                command=command,
                required=True,
                unavailable_status=ConformanceStatus.NOT_IMPLEMENTED,
                unavailable_reason=None,
            )
        )
    return tuple(selected)
