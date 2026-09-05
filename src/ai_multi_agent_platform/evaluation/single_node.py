"""Production-shaped Evaluation composition for the built-in single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.agents.runtime import AgentRuntime
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.distributed.runtime import DistributedRuntime
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.execution import ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.models import ModelRegistry, ModelRuntime
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository, WorkspaceProvider

from .agent_evidence import AgentRunEvidenceCaseExecutor
from .behavior_evidence import (
    ApprovalEvidenceCaseExecutor,
    ApprovalRecordReader,
    DistributedRuntimeEvidenceCaseExecutor,
)
from .contracts import EvaluationCaseExecutor, EvaluatorLike
from .evaluators import DeterministicAssertionEvaluator, MetricThresholdEvaluator
from .evidence import (
    CompositeEvaluationEvidenceProvider,
    EvaluationEvidenceProvider,
    EvidenceEnrichingCaseExecutor,
)
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
from .product import (
    AgentTargetValidatingCaseExecutor,
    DirectoryEvaluationFixtureResolver,
    EvaluationTargetSnapshotEnricher,
    TargetAwareEvaluationService,
    evaluation_task_metadata,
    load_evaluation_assets,
)
from .reference import KernelEvaluationCaseExecutor
from .rubric import ObservationRubricEvaluator
from .runner import EvaluationRunner
from .service import EvaluationService
from .sqlite_repository import SqliteEvaluationRepository
from .suite_assets import SqliteEvaluationSuiteAssetRepository
from .workspace import WorkspaceEvaluationIsolation

_SINGLE_NODE_EVALUATION_OWNER = "evaluation-single-node"


@dataclass(frozen=True, slots=True)
class SingleNodeEvaluationComposition:
    repository: SqliteEvaluationRepository
    suite_assets: SqliteEvaluationSuiteAssetRepository
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
    asset_dir: str | Path,
    kernel: PlatformKernel,
    agents: AgentRepository,
    agent_runtime: AgentRuntime,
    models: ModelRegistry,
    model_runtime: ModelRuntime,
    orchestrator: ReferenceOrchestrator,
    executor: ReferenceExecutor,
    files: FileProvider,
    workspaces: WorkspaceProvider,
    project_id: str,
    run_workspace_bindings: RunWorkspaceBindingRepository,
    evidence_providers: tuple[EvaluationEvidenceProvider, ...] = (),
    approval_reader: ApprovalRecordReader | None = None,
    distributed_runtime: DistributedRuntime | None = None,
) -> SingleNodeEvaluationComposition:
    """Build durable configurable Evaluation over the canonical single-node runtimes."""

    assets = load_evaluation_assets(asset_dir)
    suites = (_reference_suite(), *assets.suites)
    policies = (_reference_policy(), *assets.regression_policies)
    owner_ref = OwnerRef(type="service", id=_SINGLE_NODE_EVALUATION_OWNER)
    repository = SqliteEvaluationRepository(database_path)
    suite_assets = SqliteEvaluationSuiteAssetRepository(database_path)
    fixture_resolver = DirectoryEvaluationFixtureResolver(
        fixture_root=Path(asset_dir) / "fixtures",
        files=files,
        project_id=project_id,
        owner_ref=owner_ref,
    )
    isolation = WorkspaceEvaluationIsolation(
        workspace_provider=workspaces,
        project_id=project_id,
        owner_ref=owner_ref,
        actor_ref=f"service:{_SINGLE_NODE_EVALUATION_OWNER}",
        fixture_resolver=fixture_resolver,
    )
    kernel_executor = KernelEvaluationCaseExecutor(
        kernel=kernel,
        owner_type="service",
        owner_id=_SINGLE_NODE_EVALUATION_OWNER,
        project_id=project_id,
        source="single-node-evaluation",
        poll_interval_seconds=0.001,
        run_workspace_bindings=run_workspace_bindings,
        task_metadata_factory=evaluation_task_metadata,
    )
    case_executor: EvaluationCaseExecutor = AgentTargetValidatingCaseExecutor(
        kernel_executor,
        agents,
        models,
    )
    if evidence_providers:
        case_executor = EvidenceEnrichingCaseExecutor(
            case_executor,
            CompositeEvaluationEvidenceProvider(evidence_providers),
        )
    if approval_reader is not None:
        case_executor = ApprovalEvidenceCaseExecutor(case_executor, approval_reader)
    if distributed_runtime is not None:
        case_executor = DistributedRuntimeEvidenceCaseExecutor(case_executor, distributed_runtime)
    case_executor = AgentRunEvidenceCaseExecutor(case_executor, agents)

    evaluators: list[EvaluatorLike] = [
        DeterministicAssertionEvaluator(),
        MetricThresholdEvaluator(),
    ]
    if any(case.rubric for suite in suites for case in suite.cases):
        if assets.model_judge is None:
            evaluators.append(ObservationRubricEvaluator())
        else:
            evaluators.append(assets.model_judge.build(model_runtime))

    runner = EvaluationRunner(
        repository=repository,
        executor=case_executor,
        evaluators=tuple(evaluators),
        isolation=isolation,
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
    service = TargetAwareEvaluationService(
        repository=repository,
        runner=runner,
        suites=suites,
        policies=policies,
        aggregation_policies=assets.aggregation_policies,
        target_enricher=EvaluationTargetSnapshotEnricher(
            agents=agent_runtime,
            models=models,
        ),
    )
    service.attach_suite_assets(suite_assets)
    return SingleNodeEvaluationComposition(
        repository=repository,
        suite_assets=suite_assets,
        service=service,
    )


__all__ = ["SingleNodeEvaluationComposition", "build_single_node_evaluation"]
