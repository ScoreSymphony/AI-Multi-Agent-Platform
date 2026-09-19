from __future__ import annotations

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios


def test_client_consistency_scenarios_bind_task_run_result_parity() -> None:
    scenarios = {
        scenario.scenario_id: scenario for scenario in profile_scenarios(ConformanceProfile.FAST)
    }
    cli = scenarios["J-cli"]
    web = scenarios["J-web"]

    assert cli.required is True
    assert web.required is True
    assert cli.command is not None
    assert web.command is not None
    assert "Task/Run/Result" in cli.criterion
    assert "Task/Run/Result" in web.criterion
    assert "pagination/filter/sort" in cli.criterion
    assert "pagination/filter/sort" in web.criterion
    assert "error/status categories" in cli.criterion
    assert "error/status categories" in web.criterion
    assert "authorization/approval" in cli.criterion
    assert "mutation idempotency" in cli.criterion
    assert "first-run command identity" in cli.criterion
    assert "authorization/approval" in web.criterion
    assert "mutation idempotency" in web.criterion
    assert "deep-link resolution" in web.criterion
    assert "reload behavior" in web.criterion
    assert "first-run command identity" in web.criterion
    cli_command = " ".join(cli.command)
    assert "test_cli_reads_shared_canonical_task_run_result_state" in cli_command
    assert "test_cli_preserves_shared_task_query_pagination_and_error_semantics" in cli_command
    assert "test_cli_core_lifecycle_mutations_use_the_shared_public_routes" in cli_command
    assert (
        "test_cli_mutation_uses_idempotency_key_and_does_not_retry_retryable_error" in cli_command
    )
    assert "test_cli_preserves_not_found_and_conflict_error_categories" in cli_command
    assert "test_cli_and_public_api_share_the_same_canonical_task_state" in cli_command
    assert "test_cli_and_public_api_share_pagination_filter_sort_semantics" in cli_command
    assert "test_cli_surfaces_canonical_authorization_denial" in cli_command
    assert "test_cli_surfaces_approval_required_and_observes_approved_action" in cli_command
    assert "test_cli_multi_agent_first_run_uses_same_control_plane_command_as_web" in cli_command
    web_command = " ".join(web.command)
    assert "src/api/canonicalStateParity.test.ts" in web_command
    assert "src/api/errorPresentation.test.ts" in web_command
    assert "src/api/onboarding.multiAgent.test.ts" in web_command
