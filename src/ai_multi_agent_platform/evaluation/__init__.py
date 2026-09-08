"""Canonical evaluation and regression framework."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .planning import ReferencePlanningEvaluationCaseExecutor

from .agent_evidence import AgentRunEvidenceCaseExecutor
from .aggregation import (
    AggregatedEvaluationResult,
    AggregationMethod,
    AggregationPolicy,
    AggregationSampleRef,
    ResultAggregator,
)
from .aggregation_config import load_aggregation_policy, parse_aggregation_policy
from .behavior_evidence import (
    ApprovalEvidenceCaseExecutor,
    ApprovalRecordReader,
    DistributedRuntimeEvidenceCaseExecutor,
)
from .ci_gate import EvaluationCIGateReport, run_reference_ci_gate
from .config import (
    EvaluationBaseline,
    load_evaluation_baseline,
    load_evaluation_suite,
    load_regression_policy,
    parse_evaluation_suite,
    parse_regression_policy,
)
from .context import EvaluationExecutionContext
from .contracts import (
    AsyncEvaluator,
    EvaluationCaseExecutor,
    EvaluationHistoryRepository,
    EvaluationIsolation,
    EvaluationRepository,
    Evaluator,
    EvaluatorLike,
)
from .coordination_evidence import CoordinationEvaluationEvidenceProvider
from .evaluators import (
    DeterministicAssertionEvaluator,
    MetricThresholdEvaluator,
    SafeEvaluator,
    evaluate_safely,
)
from .evidence import (
    AccountingEvaluationEvidenceProvider,
    CompositeEvaluationEvidenceProvider,
    EvaluationEvidence,
    EvaluationEvidenceProvider,
    EvidenceEnrichingCaseExecutor,
    InMemoryObservabilityEvaluationEvidenceProvider,
    LogReferenceResolver,
)
from .hardening import (
    ResourceLimitEvaluator,
    merge_snapshot_references,
    observation_assertion_payload,
    validate_snapshot_reference_kinds,
)
from .history import EvaluationHistoryService, EvaluationTrendPoint
from .manifest_repository import (
    EvalManifestRepository,
    InMemoryEvalManifestRepository,
    SqliteEvalManifestRepository,
)
from .model_judge import ModelJudgeEvaluator
from .models import (
    EVALUATION_SCHEMA_VERSION,
    AssertionResult,
    ComparisonFinding,
    ComparisonKind,
    ComparisonOperator,
    ComparisonReport,
    ConfigurationSnapshot,
    DeterministicAssertion,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    EvaluatorDescriptor,
    EvaluatorKind,
    MetricResult,
    MetricRule,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    RubricCriterion,
    SnapshotValue,
    VersionReference,
)
from .reference import KernelEvaluationCaseExecutor
from .regression import RegressionEngine
from .repository import InMemoryEvaluationRepository
from .repository_intelligence import RepositoryIntelligenceEvaluationCaseExecutor
from .reproducibility import (
    EVAL_MANIFEST_SCHEMA_VERSION,
    CaseReproducibilitySpec,
    Comparability,
    EnvironmentFingerprint,
    EvalManifest,
    EvalManifestBuilder,
    EvalManifestContext,
    ManifestComparator,
    ManifestComparison,
    ManifestDifference,
    ManifestReference,
    RandomnessMode,
    RepeatOutcome,
    RepeatPolicy,
    RepeatStrategy,
    ReproducibleRegressionGate,
    SeedPolicy,
    decode_manifest,
    encode_manifest,
    manifest_projection,
    per_repeat_outcomes,
)
from .rubric import ObservationRubricEvaluator
from .runner import EvaluationRunner, EvaluationRunSummary, NoopEvaluationIsolation
from .service import (
    EvaluationRunDetail,
    EvaluationService,
    aggregation_policy_ref,
    evaluation_suite_ref,
    regression_policy_ref,
)
from .sqlite_repository import SqliteEvaluationRepository
from .suite_assets import (
    EvaluationSuiteAssetRepository,
    SqliteEvaluationSuiteAssetRepository,
    suite_checksum,
    suite_payload,
    suite_ref,
)
from .workspace import (
    EvaluationFixture,
    EvaluationFixtureResolver,
    ResolvedEvaluationFixtures,
    StaticEvaluationFixtureResolver,
    WorkspaceEvaluationIsolation,
)


def __getattr__(name: str) -> object:
    """Resolve optional heavy exports without creating package import cycles."""
    if name == "ReferencePlanningEvaluationCaseExecutor":
        module = import_module(f"{__name__}.planning")
        return module.ReferencePlanningEvaluationCaseExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "EVAL_MANIFEST_SCHEMA_VERSION",
    "AccountingEvaluationEvidenceProvider",
    "AgentRunEvidenceCaseExecutor",
    "AggregatedEvaluationResult",
    "AggregationMethod",
    "AggregationPolicy",
    "AggregationSampleRef",
    "ApprovalEvidenceCaseExecutor",
    "ApprovalRecordReader",
    "AssertionResult",
    "AsyncEvaluator",
    "CaseReproducibilitySpec",
    "Comparability",
    "ComparisonFinding",
    "ComparisonKind",
    "ComparisonOperator",
    "ComparisonReport",
    "CompositeEvaluationEvidenceProvider",
    "ConfigurationSnapshot",
    "CoordinationEvaluationEvidenceProvider",
    "DeterministicAssertion",
    "DeterministicAssertionEvaluator",
    "DistributedRuntimeEvidenceCaseExecutor",
    "EnvironmentFingerprint",
    "EvalManifest",
    "EvalManifestBuilder",
    "EvalManifestContext",
    "EvalManifestRepository",
    "EvaluationAttempt",
    "EvaluationBaseline",
    "EvaluationCIGateReport",
    "EvaluationCase",
    "EvaluationCaseExecutor",
    "EvaluationEvidence",
    "EvaluationEvidenceProvider",
    "EvaluationExecutionContext",
    "EvaluationFixture",
    "EvaluationFixtureResolver",
    "EvaluationHistoryRepository",
    "EvaluationHistoryService",
    "EvaluationIsolation",
    "EvaluationObservation",
    "EvaluationOutcome",
    "EvaluationRepository",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationRunDetail",
    "EvaluationRunner",
    "EvaluationRunStatus",
    "EvaluationRunSummary",
    "EvaluationService",
    "EvaluationSuite",
    "EvaluationSuiteAssetRepository",
    "EvaluationTrendPoint",
    "Evaluator",
    "EvaluatorDescriptor",
    "EvaluatorKind",
    "EvaluatorLike",
    "EvidenceEnrichingCaseExecutor",
    "InMemoryEvalManifestRepository",
    "InMemoryEvaluationRepository",
    "InMemoryObservabilityEvaluationEvidenceProvider",
    "KernelEvaluationCaseExecutor",
    "LogReferenceResolver",
    "ManifestComparator",
    "ManifestComparison",
    "ManifestDifference",
    "ManifestReference",
    "MetricResult",
    "MetricRule",
    "MetricThresholdEvaluator",
    "ModelJudgeEvaluator",
    "NoopEvaluationIsolation",
    "ObservationRubricEvaluator",
    "RandomnessMode",
    "ReferencePlanningEvaluationCaseExecutor",
    "RegressionEngine",
    "RegressionPolicy",
    "RegressionRule",
    "RegressionRuleKind",
    "RepeatOutcome",
    "RepeatPolicy",
    "RepeatStrategy",
    "ReproducibleRegressionGate",
    "RepositoryIntelligenceEvaluationCaseExecutor",
    "ResolvedEvaluationFixtures",
    "ResourceLimitEvaluator",
    "ResultAggregator",
    "RubricCriterion",
    "SafeEvaluator",
    "SeedPolicy",
    "SnapshotValue",
    "SqliteEvalManifestRepository",
    "SqliteEvaluationRepository",
    "SqliteEvaluationSuiteAssetRepository",
    "StaticEvaluationFixtureResolver",
    "VersionReference",
    "WorkspaceEvaluationIsolation",
    "aggregation_policy_ref",
    "decode_manifest",
    "encode_manifest",
    "evaluate_safely",
    "evaluation_suite_ref",
    "load_aggregation_policy",
    "load_evaluation_baseline",
    "load_evaluation_suite",
    "load_regression_policy",
    "manifest_projection",
    "merge_snapshot_references",
    "observation_assertion_payload",
    "parse_aggregation_policy",
    "parse_evaluation_suite",
    "parse_regression_policy",
    "per_repeat_outcomes",
    "regression_policy_ref",
    "run_reference_ci_gate",
    "suite_checksum",
    "suite_payload",
    "suite_ref",
    "validate_snapshot_reference_kinds",
]
