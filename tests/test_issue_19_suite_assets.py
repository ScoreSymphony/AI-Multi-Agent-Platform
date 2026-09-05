from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationRun,
    EvaluationSuite,
    SqliteEvaluationRepository,
)
from ai_multi_agent_platform.evaluation.suite_assets import (
    SqliteEvaluationSuiteAssetRepository,
    suite_ref,
)


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="portable.example",
        name="Portable example",
        version="2.1",
        description="Durable suite asset",
        tags=("portable",),
        cases=(
            EvaluationCase(
                case_id="portable.example.case",
                name="Portable case",
                version="1.0",
                input_template={"title": "Portable evaluation"},
                fixtures=(),
            ),
        ),
    )


def test_suite_asset_is_restart_safe_and_exact_version_is_create_only(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.sqlite3"
    first = SqliteEvaluationSuiteAssetRepository(path)
    suite = _suite()

    checksum = first.create_suite(suite)
    assert checksum.startswith("sha256:")
    assert first.get_suite(suite_ref(suite)) == suite

    restarted = SqliteEvaluationSuiteAssetRepository(path)
    assert restarted.get_suite(suite_ref(suite)) == suite
    assert restarted.list_suites() == (suite,)

    with pytest.raises(ContractError) as exc_info:
        restarted.create_suite(suite)
    assert exc_info.value.code is ErrorCode.CONFLICT


def test_suite_asset_compensation_checks_checksum_and_run_history(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.sqlite3"
    assets = SqliteEvaluationSuiteAssetRepository(path)
    history = SqliteEvaluationRepository(path)
    suite = _suite()
    checksum = assets.create_suite(suite)

    with pytest.raises(ContractError) as exc_info:
        assets.delete_suite(suite_ref(suite), expected_checksum="sha256:wrong")
    assert exc_info.value.code is ErrorCode.CONFLICT
    assert assets.get_suite(suite_ref(suite)) == suite

    run = EvaluationRun(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        snapshot=ConfigurationSnapshot(platform_version="test"),
    )
    history.save_run(run)

    with pytest.raises(ContractError) as exc_info:
        assets.delete_suite(suite_ref(suite), expected_checksum=checksum)
    assert exc_info.value.code is ErrorCode.CONFLICT
    assert assets.get_suite(suite_ref(suite)) == suite


def test_unreferenced_suite_asset_can_be_compensated(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.sqlite3"
    assets = SqliteEvaluationSuiteAssetRepository(path)
    suite = _suite()
    checksum = assets.create_suite(suite)

    assets.delete_suite(suite_ref(suite), expected_checksum=checksum)

    assert assets.get_suite(suite_ref(suite)) is None
