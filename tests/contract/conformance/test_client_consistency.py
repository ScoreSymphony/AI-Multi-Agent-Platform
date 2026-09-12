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
    assert "test_cli_reads_shared_canonical_task_run_result_state" in " ".join(cli.command)
    assert "src/api/canonicalStateParity.test.ts" in " ".join(web.command)
