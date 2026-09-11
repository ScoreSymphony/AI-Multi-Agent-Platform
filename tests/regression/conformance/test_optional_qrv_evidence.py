from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile
from ai_multi_agent_platform.conformance.optional_profiles import activate_optional_scenarios


def _command_for(scenario_id: str) -> str:
    scenarios = activate_optional_scenarios(
        ConformanceProfile.RELEASE,
        enabled_optional={scenario_id},
    )
    scenario = next(item for item in scenarios if item.scenario_id == scenario_id)
    assert scenario.required is True
    assert scenario.command is not None
    return " ".join(scenario.command)


def test_q_claim_keeps_composite_revision_stability_evidence() -> None:
    command = _command_for("Q")

    assert (
        "tests/integration/templates/test_revision_stability.py::"
        "test_composite_reapply_keeps_original_revision_until_explicit_upgrade"
    ) in command
    assert "test_reapply_authorizes_instance_and_exact_source_revision" in command
    assert (
        "test_unauthorized_dependency_blocks_preview_and_apply_before_resource_creation" in command
    )


def test_r_claim_uses_public_portability_and_integrity_evidence() -> None:
    command = _command_for("R")

    assert "test_control_plane_binds_export_preview_and_import_without_client_owned_plan" in command
    assert "test_portability_cli_exports_and_previews_through_canonical_commands" in command
    assert "test_portability_cli_requires_confirmation_and_never_submits_import_plan" in command
    assert "test_file_artifact_package_round_trip_remaps_provider_and_ids" in command
    assert "test_file_snapshot_rejects_bytes_that_do_not_match_canonical_checksum" in command
    assert "test_preview_reports_existing_id_conflict_before_any_mutation" in command
    assert "test_plaintext_secret_bearing_field_is_rejected" in command
    assert "test_backend_private_runtime_state_is_rejected_recursively" in command


def test_v_claim_covers_personal_cross_org_and_historical_boundaries() -> None:
    command = _command_for("V")

    assert "test_personal_scope_does_not_require_an_organization" in command
    assert (
        "test_suspend_remove_and_role_changes_feed_scope_without_becoming_authorization" in command
    )
    assert "test_resource_ownership_sharing_revoke_and_cross_org_isolation" in command
    assert "test_cross_org_share_flag_is_not_a_substitute_for_authorization" in command
    assert "test_historical_task_and_event_identity_survive_membership_removal" in command
