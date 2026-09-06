from __future__ import annotations

import json
from pathlib import Path

from ai_multi_agent_platform.conformance import (
    REPORT_SCHEMA,
    CompatibilityResult,
    ConformanceProfile,
    ConformanceScenario,
    ConformanceStatus,
    profile_scenarios,
    run_conformance,
)


def test_conformance_profiles_are_explicit_and_scenario_ids_are_unique() -> None:
    assert {profile.value for profile in ConformanceProfile} == {
        "fast",
        "integration",
        "release",
    }
    for profile in ConformanceProfile:
        scenarios = profile_scenarios(profile)
        assert scenarios
        assert len({scenario.scenario_id for scenario in scenarios}) == len(scenarios)
        assert all(scenario.owner.startswith("#") for scenario in scenarios)
        assert all(scenario.criterion for scenario in scenarios)


def test_fast_profile_owns_the_reference_security_verification_and_client_slice() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.FAST)
    }
    assert {
        "A",
        "D-model",
        "D-capability",
        "F",
        "H",
        "J-cli",
        "J-web",
        "U",
        "ARCH",
    } <= scenarios.keys()
    assert all(scenario.required for scenario in scenarios.values())
    assert all(scenario.command is not None for scenario in scenarios.values())


def test_optional_disabled_scenario_does_not_break_reference_compatibility(tmp_path: Path) -> None:
    scenario = ConformanceScenario(
        scenario_id="S",
        owner="#81 Registry",
        criterion="Registry remains optional",
        command=None,
        required=False,
        unavailable_status=ConformanceStatus.DISABLED,
        unavailable_reason="disabled in reference profile",
    )

    report = run_conformance(
        ConformanceProfile.FAST,
        repository_root=tmp_path,
        scenarios=(scenario,),
    )

    assert report.passed is True
    assert report.compatibility_result == CompatibilityResult.COMPATIBLE.value
    assert report.scenarios[0].status == ConformanceStatus.DISABLED.value
    assert report.scenarios[0].compatibility_result == CompatibilityResult.NOT_CLAIMED.value


def test_required_unimplemented_scenario_blocks_compatibility_claim(tmp_path: Path) -> None:
    scenario = ConformanceScenario(
        scenario_id="future-required",
        owner="#46 future required scenario",
        criterion="required acceptance path exists",
        command=None,
        required=True,
        unavailable_status=ConformanceStatus.NOT_IMPLEMENTED,
        unavailable_reason="not wired yet",
    )

    report = run_conformance(
        ConformanceProfile.RELEASE,
        repository_root=tmp_path,
        scenarios=(scenario,),
    )

    assert report.passed is False
    assert report.compatibility_result == CompatibilityResult.INCOMPLETE.value
    assert report.scenarios[0].failure_category == ConformanceStatus.NOT_IMPLEMENTED.value


def test_release_profile_has_no_required_placeholders() -> None:
    scenarios = profile_scenarios(ConformanceProfile.RELEASE)
    pending_required = {
        scenario.scenario_id
        for scenario in scenarios
        if scenario.required and scenario.command is None
    }

    assert pending_required == set()


def test_release_lifecycle_checks_are_required_and_bound_to_real_evidence() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }
    expected_evidence = {
        "REL-BACKUP": "test_clean_replacement_machine_restore_preserves_canonical_history",
        "REL-UPGRADE": "test_upgrade_from_previous_schema_fixture_records_history",
        "REL-EVAL": "scripts/ci/issue19_evaluation_gate.py",
    }

    for scenario_id, marker in expected_evidence.items():
        scenario = scenarios[scenario_id]
        assert scenario.required is True
        assert scenario.command is not None
        assert marker in " ".join(scenario.command)

    upgrade_command = " ".join(scenarios["REL-UPGRADE"].command or ())
    assert "test_forward_only_migration_requires_matching_verified_backup" in upgrade_command
    assert "test_failed_upgrade_stays_in_maintenance_until_explicit_resume" in upgrade_command


def test_operational_release_paths_are_bound_to_owning_acceptance_evidence() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }
    expected_evidence = {
        "G": "test_controlled_failure_retry_preserves_canonical_history_and_retry_telemetry",
        "I": "test_one_time_schedule_creates_canonical_task_with_provenance",
        "K": "test_message_to_task_handoff_is_canonical_and_bidirectionally_linked",
        "L": "test_terminal_http_resource_and_command_use_standard_composition_and_",
        "M": "test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file",
        "O": "test_task_run_executor_accounting_is_idempotent_and_aggregated",
        "P": "test_standard_catalog_lifecycle_uses_real_control_plane_http_command_path",
        "W": "test_dependency_satisfaction_cycle_cross_project_and_blocked_reason",
    }

    for scenario_id, marker in expected_evidence.items():
        scenario = scenarios[scenario_id]
        assert scenario.required is True
        assert scenario.command is not None
        assert marker in " ".join(scenario.command)

    browser_command = " ".join(scenarios["M"].command or ())
    assert "test_download_enters_canonical_file_and_artifact_path_with_redacted_provenance" in (
        browser_command
    )
    task_management_command = " ".join(scenarios["W"].command or ())
    assert "test_priority_deadline_not_before_and_query_projection" in task_management_command
    assert (
        "test_responsibility_reassignment_and_agent_assignment_are_permission_neutral"
        in task_management_command
    )


def test_optional_profiles_are_explicit_in_integration_registry() -> None:
    scenarios = {
        scenario.scenario_id: scenario
        for scenario in profile_scenarios(ConformanceProfile.INTEGRATION)
    }
    for scenario_id in ("B", "C", "E", "S", "X"):
        scenario = scenarios[scenario_id]
        assert scenario.required is False
        assert scenario.command is None
        assert scenario.unavailable_status in {
            ConformanceStatus.DISABLED,
            ConformanceStatus.UNSUPPORTED,
        }
        assert scenario.unavailable_reason


def test_report_schema_records_version_and_evidence_fields(tmp_path: Path) -> None:
    scenario = ConformanceScenario(
        scenario_id="S",
        owner="#81 Registry",
        criterion="optional reference fixture",
        command=None,
        required=False,
        unavailable_status=ConformanceStatus.DISABLED,
        unavailable_reason="disabled",
    )
    report = run_conformance(
        ConformanceProfile.FAST,
        repository_root=tmp_path,
        deployment_profile="reference-single-node",
        adapter_versions={"example-adapter": "1.2.3"},
        provider_versions={"example-provider": "4.5.6"},
        plugin_versions={"example-plugin": "7.8.9"},
        scenarios=(scenario,),
    )
    payload = json.loads(report.to_json())

    assert payload["schema"] == REPORT_SCHEMA
    assert payload["deployment_profile"] == "reference-single-node"
    assert payload["adapter_versions"] == [{"name": "example-adapter", "version": "1.2.3"}]
    assert payload["provider_versions"] == [{"name": "example-provider", "version": "4.5.6"}]
    assert payload["plugin_versions"] == [{"name": "example-plugin", "version": "7.8.9"}]
    assert payload["scenarios"][0]["canonical_resource_ids"] == []
    assert payload["scenarios"][0]["evidence"] == []
    assert payload["scenarios"][0]["failure_category"] == "disabled"
