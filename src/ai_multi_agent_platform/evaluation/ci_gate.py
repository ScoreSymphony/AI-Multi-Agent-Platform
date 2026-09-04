"""Deterministic no-paid-service CI gate built on the canonical evaluation runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.contracts import ProviderDescriptor
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator

from .config import (
    EvaluationBaseline,
    load_evaluation_baseline,
    load_evaluation_suite,
    load_regression_policy,
)
from .evaluators import DeterministicAssertionEvaluator, MetricThresholdEvaluator
from .models import (
    ComparisonFinding,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationSuite,
    EvaluatorDescriptor,
    RegressionPolicy,
    SnapshotValue,
    VersionReference,
)
from .reference import KernelEvaluationCaseExecutor
from .repository import InMemoryEvaluationRepository
from .runner import EvaluationRunner, EvaluationRunSummary

_REFERENCE_WORKSPACE = "evaluation-ci"
_REFERENCE_OWNER = "evaluation-ci"
_REFERENCE_SEED = 19


@dataclass(frozen=True, slots=True)
class EvaluationCIGateReport:
    """Outcome of the deterministic PR gate with canonical run/comparison evidence."""

    summary: EvaluationRunSummary
    suite: EvaluationSuite
    policy: RegressionPolicy
    baseline: EvaluationBaseline

    @property
    def failed_results(self) -> tuple[EvaluationResult, ...]:
        return tuple(
            result for result in self.summary.results if result.outcome is not EvaluationOutcome.PASSED
        )

    @property
    def regressions(self) -> tuple[ComparisonFinding, ...]:
        comparison = self.summary.comparison
        return () if comparison is None else comparison.regressions

    @property
    def passed(self) -> bool:
        return not self.failed_results and not self.regressions

    def diagnostics(self) -> tuple[str, ...]:
        messages: list[str] = []
        for result in self.failed_results:
            detail = result.error_message or result.outcome.value
            messages.append(
                f"result {result.case_id}/{result.evaluator.evaluator_id}: {detail}"
            )
        for finding in self.regressions:
            messages.append(
                f"regression {finding.rule_id}/{finding.case_id}: {finding.message}"
            )
        return tuple(messages)


def _provider_reference(
    *,
    kind: str,
    descriptor: ProviderDescriptor,
    platform_version: str,
    platform_commit: str | None,
) -> VersionReference:
    return VersionReference(
        kind=kind,
        ref_id=descriptor.provider_id,
        version=platform_version,
        revision=platform_commit,
    )


def _evaluator_reference(
    descriptor: EvaluatorDescriptor,
    *,
    platform_commit: str | None,
) -> VersionReference:
    return VersionReference(
        kind="evaluator",
        ref_id=descriptor.evaluator_id,
        version=descriptor.version,
        revision=platform_commit,
    )


def _validate_reference_suite(suite: EvaluationSuite) -> None:
    if not suite.cases:
        raise ValueError("deterministic CI evaluation suite must contain at least one case")
    for case in suite.cases:
        if case.fixtures:
            raise ValueError(
                f"deterministic reference CI case {case.case_id!r} declares fixtures; "
                "use workspace-backed isolation instead of the no-fixture reference gate"
            )
        if case.rubric:
            raise ValueError(
                f"deterministic reference CI case {case.case_id!r} declares rubric criteria; "
                "model/rubric judging is not part of the no-paid PR gate"
            )
        if case.resource_limits:
            raise ValueError(
                f"deterministic reference CI case {case.case_id!r} declares resource limits "
                "that the reference gate cannot enforce"
            )


def _validate_baseline_coverage(
    *,
    baseline: EvaluationBaseline,
    suite: EvaluationSuite,
    evaluators: tuple[DeterministicAssertionEvaluator | MetricThresholdEvaluator, ...],
) -> None:
    if baseline.run.repetitions != 1:
        raise ValueError("deterministic CI baseline must use exactly one repetition")
    if any(result.outcome is not EvaluationOutcome.PASSED for result in baseline.results):
        raise ValueError("accepted deterministic CI baseline must contain only passing results")
    expected = {
        (case.case_id, evaluator.descriptor.evaluator_id)
        for case in suite.cases
        for evaluator in evaluators
    }
    actual = {(result.case_id, result.evaluator.evaluator_id) for result in baseline.results}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise ValueError(
            "deterministic CI baseline coverage does not match suite/evaluators: "
            f"missing={missing}, unexpected={unexpected}"
        )


async def run_reference_ci_gate(
    *,
    suite_path: str | Path,
    policy_path: str | Path,
    baseline_path: str | Path,
    workspace_root: str | Path,
    platform_version: str,
    platform_commit: str | None = None,
) -> EvaluationCIGateReport:
    """Execute the checked-in deterministic suite against its accepted canonical baseline."""

    suite = load_evaluation_suite(suite_path)
    policy = load_regression_policy(policy_path)
    baseline = load_evaluation_baseline(baseline_path, suite=suite)
    _validate_reference_suite(suite)

    evaluators = (DeterministicAssertionEvaluator(), MetricThresholdEvaluator())
    _validate_baseline_coverage(baseline=baseline, suite=suite, evaluators=evaluators)

    repository = InMemoryEvaluationRepository()
    repository.save_run(baseline.run)
    for result in baseline.results:
        repository.save_result(result)

    root = Path(workspace_root)
    reference_executor = ReferenceExecutor(root)
    ExecutorLifecycleBackend.ensure_workspace(root, _REFERENCE_WORKSPACE)
    lifecycle = ExecutorLifecycleBackend(
        reference_executor,
        workspace=_REFERENCE_WORKSPACE,
        action="echo",
    )
    orchestrator = ReferenceOrchestrator()
    kernel = PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
    )
    case_executor = KernelEvaluationCaseExecutor(
        kernel=kernel,
        owner_type="service",
        owner_id=_REFERENCE_OWNER,
        source="evaluation-ci-gate",
        poll_interval_seconds=0.001,
    )
    runner = EvaluationRunner(
        repository=repository,
        executor=case_executor,
        evaluators=evaluators,
    )

    snapshot = baseline.run.snapshot.__class__(
        platform_version=platform_version,
        platform_commit=platform_commit,
        references=(
            _provider_reference(
                kind="orchestrator",
                descriptor=orchestrator.descriptor,
                platform_version=platform_version,
                platform_commit=platform_commit,
            ),
            VersionReference(
                kind="executor",
                ref_id=reference_executor.descriptor.executor_id,
                version=platform_version,
                revision=platform_commit,
            ),
            *(
                _evaluator_reference(
                    evaluator.descriptor,
                    platform_commit=platform_commit,
                )
                for evaluator in evaluators
            ),
            VersionReference(
                kind="regression_policy",
                ref_id=policy.policy_id,
                version=policy.version,
            ),
            VersionReference(
                kind="evaluation_suite",
                ref_id=suite.suite_id,
                version=suite.version,
            ),
        ),
        environment=(SnapshotValue(key="evaluation_profile", value="pr-deterministic"),),
    )
    summary = await runner.run_suite(
        suite=suite,
        snapshot=snapshot,
        repetitions=1,
        seed=_REFERENCE_SEED,
        baseline_run_id=baseline.run.run_id,
        regression_policy=policy,
    )
    return EvaluationCIGateReport(
        summary=summary,
        suite=suite,
        policy=policy,
        baseline=baseline,
    )


__all__ = ["EvaluationCIGateReport", "run_reference_ci_gate"]
