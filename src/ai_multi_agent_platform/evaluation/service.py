"""Application service for configured evaluation suites and durable comparison history."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .contracts import EvaluationHistoryRepository
from .models import (
    ComparisonReport,
    ConfigurationSnapshot,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    RegressionPolicy,
)
from .regression import RegressionEngine
from .runner import EvaluationRunner, EvaluationRunSummary


def evaluation_suite_ref(suite: EvaluationSuite) -> str:
    """Stable northbound reference for one exact suite version."""

    return f"{suite.suite_id}@{suite.version}"


def regression_policy_ref(policy: RegressionPolicy) -> str:
    """Stable northbound reference for one exact regression-policy version."""

    return f"{policy.policy_id}@{policy.version}"


@dataclass(frozen=True, slots=True)
class EvaluationRunDetail:
    """One durable run with the evidence currently addressable through the repository."""

    run: EvaluationRun
    results: tuple[EvaluationResult, ...]
    comparison: ComparisonReport | None


class EvaluationService:
    """Configured application boundary used by Control Plane and later CLI surfaces.

    Suites and policies are deployment/configuration assets. Execution remains owned by
    ``EvaluationRunner`` and persistence remains owned by ``EvaluationHistoryRepository``.
    """

    def __init__(
        self,
        *,
        repository: EvaluationHistoryRepository,
        runner: EvaluationRunner,
        suites: tuple[EvaluationSuite, ...],
        policies: tuple[RegressionPolicy, ...] = (),
        regression_engine: RegressionEngine | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._regression_engine = regression_engine or RegressionEngine()
        self._suites = self._index_suites(suites)
        self._policies = self._index_policies(policies)

    @staticmethod
    def _index_suites(suites: tuple[EvaluationSuite, ...]) -> dict[str, EvaluationSuite]:
        indexed: dict[str, EvaluationSuite] = {}
        for suite in suites:
            ref = evaluation_suite_ref(suite)
            if ref in indexed:
                raise ValueError(f"duplicate configured evaluation suite: {ref}")
            indexed[ref] = suite
        return indexed

    @staticmethod
    def _index_policies(
        policies: tuple[RegressionPolicy, ...],
    ) -> dict[str, RegressionPolicy]:
        indexed: dict[str, RegressionPolicy] = {}
        for policy in policies:
            ref = regression_policy_ref(policy)
            if ref in indexed:
                raise ValueError(f"duplicate configured regression policy: {ref}")
            indexed[ref] = policy
        return indexed

    def list_suites(self) -> tuple[EvaluationSuite, ...]:
        return tuple(self._suites[key] for key in sorted(self._suites))

    def get_suite(self, suite_ref: str) -> EvaluationSuite:
        try:
            return self._suites[suite_ref]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"evaluation suite not found: {suite_ref}",
            ) from exc

    def get_policy(self, policy_ref: str) -> RegressionPolicy:
        try:
            return self._policies[policy_ref]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"regression policy not found: {policy_ref}",
            ) from exc

    def list_runs(self, *, limit: int = 100) -> tuple[EvaluationRun, ...]:
        return self._repository.list_runs(limit=limit)

    def get_run_detail(self, run_id: str) -> EvaluationRunDetail:
        run = self._repository.get_run(run_id)
        if run is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"evaluation run not found: {run_id}")
        return EvaluationRunDetail(
            run=run,
            results=self._repository.list_results(run_id),
            comparison=self._repository.get_comparison(run_id),
        )

    async def run_suite(
        self,
        *,
        suite_ref: str,
        snapshot: ConfigurationSnapshot,
        repetitions: int = 1,
        seed: int | None = None,
        baseline_run_id: str | None = None,
        regression_policy_ref_value: str | None = None,
    ) -> EvaluationRunSummary:
        suite = self.get_suite(suite_ref)
        policy = (
            None
            if regression_policy_ref_value is None
            else self.get_policy(regression_policy_ref_value)
        )
        return await self._runner.run_suite(
            suite=suite,
            snapshot=snapshot,
            repetitions=repetitions,
            seed=seed,
            baseline_run_id=baseline_run_id,
            regression_policy=policy,
        )

    def compare_runs(
        self,
        *,
        current_run_id: str,
        baseline_run_id: str,
        regression_policy_ref_value: str,
    ) -> ComparisonReport:
        current = self._repository.get_run(current_run_id)
        baseline = self._repository.get_run(baseline_run_id)
        if current is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"current evaluation run not found: {current_run_id}",
            )
        if baseline is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"baseline evaluation run not found: {baseline_run_id}",
            )
        if current.status is not EvaluationRunStatus.COMPLETED:
            raise ValueError("current evaluation run must be completed")
        if baseline.status is not EvaluationRunStatus.COMPLETED:
            raise ValueError("baseline evaluation run must be completed")
        if (current.suite_id, current.suite_version) != (
            baseline.suite_id,
            baseline.suite_version,
        ):
            raise ValueError("evaluation runs must use the same suite identity/version")
        if current.repetitions != 1 or baseline.repetitions != 1:
            raise ValueError(
                "manual comparison currently requires single-repetition runs until aggregation exists"
            )

        policy = self.get_policy(regression_policy_ref_value)
        comparison = self._regression_engine.compare(
            baseline_run_id=baseline_run_id,
            current_run_id=current_run_id,
            baseline_results=self._repository.list_results(baseline_run_id),
            current_results=self._repository.list_results(current_run_id),
            policy=policy,
        )
        self._repository.save_comparison(comparison)
        return comparison
