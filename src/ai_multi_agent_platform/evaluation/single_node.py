"""Production-shaped Evaluation composition for the built-in single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.execution import ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator

from .agent_evidence import AgentRunEvidenceCaseExecutor
from .evaluators import DeterministicAssertionEvaluator, MetricThresholdEvaluator
from .models import (
    ComparisonOperator,
    DeterministicAssertion,
    EvaluationCase,
    EvaluationSuite,
    MetricRule,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    SnapshotValue,
    VersionReference,
)
from .reference import KernelEvaluationCaseExecutor
from .runner import EvaluationRunner
from .service import EvaluationService
from .sqlite_repository import SqliteEvaluationRepository

_SINGLE_NODE_EVALUATION_OWNER = "evaluation-single-node"


@dataclass(frozen=True, slots=True)
class SingleNodeEvaluationComposition:
    repository: SqliteEvaluationRepository
    service: EvaluationService


def _reference_suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="single-node.reference.lifecycle",
        name="Single-node canonical lifecycle",
        version="1.0",
        description=(
            "Durable no-paid reference evaluation through the same canonical Task/Run path "
            "used by the single-node deployment."
        ),
        tags=("reference", "single-node", "critical"),
        cases=(
            EvaluationCase(
                case_id="single-node.reference.lifecycle.success",
                name="Canonical Task/Run lifecycle succeeds",
                version="1.0",
                input_template={
                    "title": "Single-node evaluation reference task",
                    "objective": "Exercise the canonical local Task/Run lifecycle",
                },
                assertions=(
                    DeterministicAssertion(
                        "task-succeeded",
                        "task.status",
                        ComparisonOperator.EQ,
                        expected="succeeded",
                    ),
                    DeterministicAssertion(
                        "run-succeeded",
                        "run.status",
                        ComparisonOperator.EQ,
                        expected="succeeded",
                    ),
                    DeterministicAssertion(
                        "run-output",
                        "run.output",
                        ComparisonOperator.EXISTS,
                    ),
                    DeterministicAssertion(
                        "run-succeeded-event",
                        "behavior.event_types",
                        ComparisonOperator.CONTAINS,
                        expected="run.succeeded",
                    ),
                ),
                metric_rules=(
                    MetricRule(
                        "single-dispatch-metric",
                        "dispatch_attempts",
                        ComparisonOperator.LTE,
                        1.0,
                        unit="attempts",
                    ),
                ),
                resource_limits=(SnapshotValue("dispatch_attempts", "1"),),
                timeout_seconds=5.0,
                tags=("reference", "single-node", "critical"),
                category="core-lifecycle",
                difficulty="reference",
            ),
        ),
    )


def _reference_policy() -> RegressionPolicy:
    return RegressionPolicy(
        policy_id="single-node.reference.regression",
        version="1.0",
        rules=(
            RegressionRule(
                "deterministic-pass-to-fail",
                RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
            ),
            RegressionRule(
                "critical-case-failure",
                RegressionRuleKind.TAGGED_CASE_FAILURE,
                tag="critical",
            ),
            RegressionRule(
                "score-drop",
                RegressionRuleKind.SCORE_DROP,
                threshold=0.0,
            ),
        ),
    )


def build_single_node_evaluation(
    *,
    database_path: str | Path,
    kernel: PlatformKernel,
    agents: AgentRepository,
    orchestrator: ReferenceOrchestrator,
    executor: ReferenceExecutor,
) -> SingleNodeEvaluationComposition:
    """Build durable Evaluation state and a runnable first-party reference suite."""

    repository = SqliteEvaluationRepository(database_path)
    kernel_executor = KernelEvaluationCaseExecutor(
        kernel=kernel,
        owner_type="service",
        owner_id=_SINGLE_NODE_EVALUATION_OWNER,
        source="single-node-evaluation",
        poll_interval_seconds=0.001,
    )
    case_executor = AgentRunEvidenceCaseExecutor(kernel_executor, agents)
    runner = EvaluationRunner(
        repository=repository,
        executor=case_executor,
        evaluators=(DeterministicAssertionEvaluator(), MetricThresholdEvaluator()),
        configuration_references=(
            VersionReference(
                kind="orchestrator",
                ref_id=orchestrator.descriptor.provider_id,
                version=__version__,
            ),
            VersionReference(
                kind="executor",
                ref_id=executor.descriptor.executor_id,
                version=__version__,
            ),
        ),
        required_snapshot_kinds=(
            "orchestrator",
            "executor",
            "evaluation_suite",
            "evaluator",
        ),
    )
    service = EvaluationService(
        repository=repository,
        runner=runner,
        suites=(_reference_suite(),),
        policies=(_reference_policy(),),
    )
    return SingleNodeEvaluationComposition(repository=repository, service=service)


__all__ = ["SingleNodeEvaluationComposition", "build_single_node_evaluation"]
