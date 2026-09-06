from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.cli.conformance import (
    _parse_component_versions,
    _parse_optional,
)
from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    ConformanceStatus,
    activate_optional_scenarios,
    optional_evidence_ids,
    profile_scenarios,
    run_conformance,
)


def _by_id(profile: ConformanceProfile, enabled: tuple[str, ...] = ()):
    return {
        scenario.scenario_id: scenario for scenario in activate_optional_scenarios(profile, enabled)
    }


def test_optional_claims_remain_disabled_by_default() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }
    for scenario_id in ("B", "C", "E", "N", "Q", "R", "S", "T", "V", "X", "Y"):
        scenario = scenarios[scenario_id]
        assert scenario.required is False
        assert scenario.command is None
        assert scenario.unavailable_status in {
            ConformanceStatus.DISABLED,
            ConformanceStatus.UNSUPPORTED,
        }


def test_maintained_optional_evidence_registry_is_explicit() -> None:
    assert optional_evidence_ids() == (
        "B",
        "C",
        "E",
        "N",
        "Q",
        "R",
        "S",
        "T",
        "V",
        "X",
        "Y",
    )


def test_enabling_supported_optional_claim_makes_it_required_and_executable() -> None:
    scenarios = _by_id(
        ConformanceProfile.RELEASE,
        ("N", "Q", "R", "S", "T", "V", "X", "Y"),
    )
    for scenario_id in ("N", "Q", "R", "S", "T", "V", "X", "Y"):
        assert scenarios[scenario_id].required is True
        assert scenarios[scenario_id].command is not None
        assert scenarios[scenario_id].unavailable_reason is None

    assert "test_event_provider_projects_task_event_and_replay_aggregates_safely" in " ".join(
        scenarios["N"].command or ()
    )
    assert "test_reapply_authorizes_instance_and_exact_source_revision" in " ".join(
        scenarios["Q"].command or ()
    )
    assert "test_composite_failure_compensates_earlier_capability_assignment" in " ".join(
        scenarios["Q"].command or ()
    )
    assert "test_executor_rolls_back_real_team_and_agent_in_reverse_order" in " ".join(
        scenarios["R"].command or ()
    )
    assert (
        "test_default_single_node_keeps_registry_and_plugin_runtime_absent_when_unconfigured"
        in " ".join(scenarios["S"].command or ())
    )
    assert "test_signed_artifact_requires_and_accepts_authoritative_verification" in " ".join(
        scenarios["S"].command or ()
    )
    assert "test_control_plane_records_repository_input_before_start_and_on_retry" in " ".join(
        scenarios["T"].command or ()
    )
    assert "test_resource_ownership_sharing_revoke_and_cross_org_isolation" in " ".join(
        scenarios["V"].command or ()
    )
    assert (
        "test_restart_promotion_reconciles_running_work_and_preserves_worker_identity"
        in " ".join(scenarios["X"].command or ())
    )
    assert "test_sqlite_partial_fan_in_survives_restart" in " ".join(scenarios["Y"].command or ())
    assert (
        "test_lost_worker_acknowledgement_delegates_to_kernel_without_blind_redispatch"
        in " ".join(scenarios["Y"].command or ())
    )


def test_distributed_profile_covers_security_result_identity_and_trace_safe_telemetry() -> None:
    scenario = _by_id(ConformanceProfile.RELEASE, ("E",))["E"]
    command = " ".join(scenario.command or ())
    assert scenario.required is True
    assert (
        "test_dispatch_authorization_denial_releases_reservation_before_worker_execution" in command
    )
    assert (
        "test_terminal_result_is_recovered_after_restart_and_then_survives_without_worker"
        in command
    )
    assert "test_scheduler_reservation_and_dispatch_emit_correlated_safe_telemetry" in command


def test_activation_rejects_unknown_or_already_required_scenarios() -> None:
    with pytest.raises(ValueError, match="not present"):
        activate_optional_scenarios(ConformanceProfile.INTEGRATION, ("N",))
    with pytest.raises(ValueError, match="already required"):
        activate_optional_scenarios(ConformanceProfile.RELEASE, ("G",))


def test_cli_optional_and_component_version_parsing_is_deterministic() -> None:
    assert _parse_optional(["n,r", "X", " t "]) == ("N", "R", "X", "T")
    assert _parse_component_versions(
        ["hermes-agent=6327930", "forge-sidecar=00b821b"],
        "--adapter-version",
    ) == {
        "hermes-agent": "6327930",
        "forge-sidecar": "00b821b",
    }
    with pytest.raises(ValueError, match="NAME=VERSION"):
        _parse_component_versions(["missing-version"], "--adapter-version")
    with pytest.raises(ValueError, match="repeats component"):
        _parse_component_versions(["same=1", "same=2"], "--adapter-version")


def test_external_adapter_profiles_fail_closed_without_real_environment(
    tmp_path: Path, monkeypatch
) -> None:
    for variable in (
        "HERMES_UPSTREAM_DIR",
        "HERMES_UPSTREAM_REVISION",
        "FORGE_SIDECAR_BASE_URL",
        "FORGE_SIDECAR_WORKSPACE_ROOT",
    ):
        monkeypatch.delenv(variable, raising=False)

    scenarios = _by_id(ConformanceProfile.RELEASE, ("B", "C"))
    for scenario_id in ("B", "C"):
        report = run_conformance(
            ConformanceProfile.RELEASE,
            repository_root=Path.cwd(),
            scenarios=(scenarios[scenario_id],),
        )
        assert report.passed is False
        assert report.scenarios[0].status == ConformanceStatus.FAIL.value
