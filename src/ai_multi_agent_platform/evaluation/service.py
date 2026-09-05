"""Application service for configured evaluation suites and durable comparison history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .aggregation import (
    AggregatedEvaluationResult,
    AggregationPolicy,
    ComparableEvaluationResult,
    ResultAggregator,
)
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
from .suite_assets import EvaluationSuiteAssetRepository


def evaluation_suite_ref(suite: EvaluationSuite) -> str:
    """Stable northbound reference for one exact suite version."""

    return f"{suite.suite_id}@{suite.version}"


def regression_policy_ref(policy: RegressionPolicy) -> str:
    """Stable northbound reference for one exact regression-policy version."""

    return f"{policy.policy_id}@{policy.version}"


def aggregation_policy_ref(policy: AggregationPolicy) -> str:
    """Stable northbound reference for one exact aggregation-policy version."""

    return f"{policy.policy_id}@{policy.version}"


@dataclass(frozen=True, slots=True)
class EvaluationRunDetail:
    """One durable run with raw/derived evidence currently addressable through the repository."""

    run: EvaluationRun
    results: tuple[EvaluationResult, ...]
    comparison: ComparisonReport | None
    aggregates: tuple[AggregatedEvaluationResult, ...] = ()


class EvaluationService:
    """Application boundary for configured and durably imported Evaluation assets.

    Built-in/deployment suites remain immutable configuration. Optional mutable Suite
    versions are owned by ``EvaluationSuiteAssetRepository``; callers such as portability
    must mutate them through this service instead of writing Evaluation-private files.
    Execution remains owned by ``EvaluationRunner`` and run/result persistence remains
    owned by ``EvaluationHistoryRepository``.
    """

    def __init__(
        self,
        *,
        repository: EvaluationHistoryRepository,
        runner: EvaluationRunner,
        suites: tuple[EvaluationSuite, ...],
        policies: tuple[RegressionPolicy, ...] = (),
        aggregation_policies: tuple[AggregationPolicy, ...] = (),
        regression_engine: RegressionEngine | None = None,
        result_aggregator: ResultAggregator | None = None,
        suite_assets: EvaluationSuiteAssetRepository | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._regression_engine = regression_engine or RegressionEngine()
        self._result_aggregator = result_aggregator or ResultAggregator()
        self._suites = self._index_suites(suites)
        self._policies = self._index_policies(policies)
        self._aggregation_policies = self._index_aggregation_policies(aggregation_policies)
        self._suite_assets = suite_assets
        self._validate_suite_asset_collisions()

    def attach_suite_assets(self, repository: EvaluationSuiteAssetRepository) -> None:
        """Bind the owning durable Suite store before the service is exposed northbound."""

        if self._suite_assets is not None and self._suite_assets is not repository:
            raise ContractError(
                ErrorCode.CONFLICT,
                "evaluation suite asset repository is already configured",
            )
        self._suite_assets = repository
        self._validate_suite_asset_collisions()

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

    @staticmethod
    def _index_aggregation_policies(
        policies: tuple[AggregationPolicy, ...],
    ) -> dict[str, AggregationPolicy]:
        indexed: dict[str, AggregationPolicy] = {}
        for policy in policies:
            ref = aggregation_policy_ref(policy)
            if ref in indexed:
                raise ValueError(f"duplicate configured aggregation policy: {ref}")
            indexed[ref] = policy
        return indexed

    def _validate_suite_asset_collisions(self) -> None:
        if self._suite_assets is None:
            return
        collisions = sorted(
            evaluation_suite_ref(suite)
            for suite in self._suite_assets.list_suites()
            if evaluation_suite_ref(suite) in self._suites
        )
        if collisions:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "durable evaluation suite assets shadow configured suite versions",
                details={"suite_refs": cast(JsonValue, collisions)},
            )

    def list_suites(self) -> tuple[EvaluationSuite, ...]:
        indexed = dict(self._suites)
        if self._suite_assets is not None:
            for suite in self._suite_assets.list_suites():
                ref = evaluation_suite_ref(suite)
                if ref in indexed:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        f"durable evaluation suite shadows configured suite: {ref}",
                    )
                indexed[ref] = suite
        return tuple(indexed[key] for key in sorted(indexed))

    def get_suite(self, suite_ref: str) -> EvaluationSuite:
        configured = self._suites.get(suite_ref)
        if configured is not None:
            return configured
        if self._suite_assets is not None:
            durable = self._suite_assets.get_suite(suite_ref)
            if durable is not None:
                return durable
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"evaluation suite not found: {suite_ref}",
        )

    def create_suite(self, suite: EvaluationSuite) -> str:
        """Create one immutable durable Suite version and return its content checksum."""

        ref = evaluation_suite_ref(suite)
        if ref in self._suites:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"configured evaluation suite version already exists: {ref}",
                details={"suite_ref": ref},
            )
        if self._suite_assets is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "durable evaluation suite mutation is not configured",
            )
        return self._suite_assets.create_suite(suite)

    def delete_suite(self, suite_ref: str, *, expected_checksum: str | None = None) -> None:
        """Compensate a durable Suite version without deleting configured or referenced state."""

        if suite_ref in self._suites:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"configured evaluation suite cannot be deleted: {suite_ref}",
            )
        if self._suite_assets is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "durable evaluation suite mutation is not configured",
            )
        self._suite_assets.delete_suite(suite_ref, expected_checksum=expected_checksum)

    def get_policy(self, policy_ref: str) -> RegressionPolicy:
        try:
            return self._policies[policy_ref]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"regression policy not found: {policy_ref}",
            ) from exc

    def get_aggregation_policy(self, policy_ref: str) -> AggregationPolicy:
        try:
            return self._aggregation_policies[policy_ref]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"aggregation policy not found: {policy_ref}",
            ) from exc

    def list_runs(self, *, limit: int | None = 100) -> tuple[EvaluationRun, ...]:
        return self._repository.list_runs(limit=limit)

    def get_run_detail(self, run_id: str) -> EvaluationRunDetail:
        run = self._repository.get_run(run_id)
        if run is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"evaluation run not found: {run_id}")
        return EvaluationRunDetail(
            run=run,
            results=self._repository.list_results(run_id),
            comparison=self._repository.get_comparison(run_id),
            aggregates=self._repository.list_aggregates(run_id),
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
        aggregation_policy_ref_value: str | None = None,
    ) -> EvaluationRunSummary:
        suite = self.get_suite(suite_ref)
        policy = (
            None
            if regression_policy_ref_value is None
            else self.get_policy(regression_policy_ref_value)
        )
        aggregation_policy = (
            None
            if aggregation_policy_ref_value is None
            else self.get_aggregation_policy(aggregation_policy_ref_value)
        )
        return await self._runner.run_suite(
            suite=suite,
            snapshot=snapshot,
            repetitions=repetitions,
            seed=seed,
            baseline_run_id=baseline_run_id,
            regression_policy=policy,
            aggregation_policy=aggregation_policy,
        )

    def compare_runs(
        self,
        *,
        current_run_id: str,
        baseline_run_id: str,
        regression_policy_ref_value: str,
        aggregation_policy_ref_value: str | None = None,
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

        aggregation_policy = (
            None
            if aggregation_policy_ref_value is None
            else self.get_aggregation_policy(aggregation_policy_ref_value)
        )
        if (current.repetitions != 1 or baseline.repetitions != 1) and aggregation_policy is None:
            raise ValueError(
                "comparison of repeated evaluation runs requires an aggregation policy"
            )
        if (
            aggregation_policy is not None
            and aggregation_policy.require_equal_sample_count
            and current.repetitions != baseline.repetitions
        ):
            raise ValueError(
                "aggregation policy requires baseline and current runs to use the same "
                "repetition count"
            )

        baseline_comparable: tuple[ComparableEvaluationResult, ...]
        current_comparable: tuple[ComparableEvaluationResult, ...]
        if aggregation_policy is None:
            baseline_comparable = self._repository.list_results(baseline_run_id)
            current_comparable = self._repository.list_results(current_run_id)
        else:
            baseline_comparable = self._aggregates_for_run(baseline, aggregation_policy)
            current_comparable = self._aggregates_for_run(current, aggregation_policy)

        policy = self.get_policy(regression_policy_ref_value)
        comparison = self._regression_engine.compare(
            baseline_run_id=baseline_run_id,
            current_run_id=current_run_id,
            baseline_results=baseline_comparable,
            current_results=current_comparable,
            policy=policy,
        )
        self._repository.save_comparison(comparison)
        return comparison

    def _aggregates_for_run(
        self,
        run: EvaluationRun,
        policy: AggregationPolicy,
    ) -> tuple[AggregatedEvaluationResult, ...]:
        aggregates = self._result_aggregator.aggregate(
            results=self._repository.list_results(run.run_id),
            policy=policy,
            expected_repetitions=run.repetitions,
        )
        for aggregate in aggregates:
            self._repository.save_aggregate(aggregate)
        return aggregates
