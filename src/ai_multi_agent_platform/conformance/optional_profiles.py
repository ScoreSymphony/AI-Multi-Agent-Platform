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
        "-m",
        "ai_multi_agent_platform.conformance.external_profile",
        profile_id,
    )


_OPTIONAL_EVIDENCE: dict[str, tuple[str, ...]] = {
    "B": _external("B"),
    "C": _external("C"),
    "E": _pytest(
        "tests/test_issue_14_security_result_recovery.py::"
        "test_dispatch_authorization_receives_canonical_worker_context_and_grant",
        "tests/test_issue_14_security_result_recovery.py::"
        "test_terminal_result_recovery_and_timeout_diagnostics_are_canonical",
        "tests/test_issue_14_distributed_telemetry.py::"
        "test_scheduler_reservation_and_dispatch_emit_correlated_safe_telemetry",
    ),
    "N": _pytest(
        "tests/test_issue75_control_plane.py::"
        "test_control_plane_inbox_is_recipient_scoped_and_commands_are_idempotent",
        "tests/test_issue75_control_plane.py::"
        "test_authenticated_http_ignores_spoofed_owner_headers_for_notification_inbox",
        "tests/test_issue75_control_plane.py::"
        "test_event_provider_projects_task_event_and_replay_aggregates_safely",
    ),
    "R": _pytest(
        "tests/test_portability.py::test_portable_package_round_trip_preserves_manifest_and_integrity",
        "tests/test_portability.py::test_plaintext_secret_bearing_field_is_rejected",
        "tests/test_portability.py::test_backend_private_runtime_state_is_rejected_recursively",
        "tests/test_issue_79_import_executor.py::"
        "test_successful_import_persists_mapping_and_imported_resources",
        "tests/test_issue_79_import_executor.py::"
        "test_failure_rolls_back_applied_resources_and_tracks_recovery",
    ),
    "T": _pytest(
        "tests/test_issue_82_repository_run_integration.py::"
        "test_task_run_materializes_repository_revision_and_returns_change_artifact",
        "tests/test_issue_82_repository_run_integration.py::"
        "test_retry_run_preserves_repository_provenance_identity",
        "tests/test_issue_82_repository_run_integration.py::"
        "test_provider_specific_ids_remain_external_metadata",
    ),
    "V": _pytest(
        "tests/test_issue_87_organization_domain.py::"
        "test_suspend_remove_and_role_changes_feed_scope_without_becoming_authorization",
        "tests/test_issue_87_organization_domain.py::"
        "test_resource_ownership_sharing_revoke_and_cross_org_isolation",
    ),
    "X": _pytest(
        "tests/test_issue_89_control_plane_ha.py::"
        "test_active_passive_promotion_fences_stale_old_leader",
        "tests/test_issue_89_failover_reconciliation.py::"
        "test_restart_promotion_reconciles_running_work_and_preserves_worker_identity",
        "tests/test_issue_89_final_failover_acceptance.py::"
        "test_duplicate_command_replay_after_promotion_does_not_duplicate_task_or_run",
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
                f"scenario {scenario.scenario_id} is already required by the {profile.value} profile"
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
