"""Canonical evaluation and regression value types for issue #19."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

EVALUATION_SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


class EvaluationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class EvaluationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluatorKind(StrEnum):
    DETERMINISTIC = "deterministic"
    METRIC = "metric"
    RUBRIC = "rubric"
    MODEL_JUDGE = "model_judge"


class ComparisonOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class RegressionRuleKind(StrEnum):
    DETERMINISTIC_PASS_TO_FAIL = "deterministic_pass_to_fail"
    SCORE_DROP = "score_drop"
    TAGGED_CASE_FAILURE = "tagged_case_failure"
    METRIC_THRESHOLD = "metric_threshold"


class ComparisonKind(StrEnum):
    REGRESSION = "regression"
    IMPROVEMENT = "improvement"


@dataclass(frozen=True, slots=True)
class VersionReference:
    """Versioned identity for one component participating in an evaluation run."""

    kind: str
    ref_id: str
    version: str
    revision: str | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("version reference kind must not be blank")
        if not self.ref_id.strip():
            raise ValueError("version reference ref_id must not be blank")
        if not self.version.strip():
            raise ValueError("version reference version must not be blank")
        if self.revision is not None and not self.revision.strip():
            raise ValueError("version reference revision must not be blank when provided")


@dataclass(frozen=True, slots=True)
class SnapshotValue:
    """Small immutable metadata entry used for environment/node configuration."""

    key: str
    value: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("snapshot value key must not be blank")


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Immutable, comparable configuration identity captured for one evaluation run."""

    platform_version: str
    references: tuple[VersionReference, ...] = ()
    platform_commit: str | None = None
    environment: tuple[SnapshotValue, ...] = ()
    snapshot_id: str = field(default_factory=lambda: new_id("evaluation_snapshot"))
    schema_version: str = EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.platform_version.strip():
            raise ValueError("platform_version must not be blank")
        if self.platform_commit is not None and not self.platform_commit.strip():
            raise ValueError("platform_commit must not be blank when provided")
        identities = [(item.kind, item.ref_id) for item in self.references]
        if len(identities) != len(set(identities)):
            raise ValueError("configuration snapshot references must be unique by kind/ref_id")
        keys = [item.key for item in self.environment]
        if len(keys) != len(set(keys)):
            raise ValueError("configuration snapshot environment keys must be unique")


@dataclass(frozen=True, slots=True)
class DeterministicAssertion:
    assertion_id: str
    path: str
    operator: ComparisonOperator
    expected: JsonValue = None
    message: str = ""

    def __post_init__(self) -> None:
        if not self.assertion_id.strip():
            raise ValueError("assertion_id must not be blank")
        if not self.path.strip():
            raise ValueError("assertion path must not be blank")
        if self.operator in {ComparisonOperator.EXISTS, ComparisonOperator.NOT_EXISTS}:
            return
        if self.operator in {
            ComparisonOperator.GT,
            ComparisonOperator.GTE,
            ComparisonOperator.LT,
            ComparisonOperator.LTE,
        } and isinstance(self.expected, bool):
            raise ValueError("numeric assertions cannot use boolean expected values")


@dataclass(frozen=True, slots=True)
class MetricRule:
    rule_id: str
    metric_name: str
    operator: ComparisonOperator
    threshold: float
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("metric rule_id must not be blank")
        if not self.metric_name.strip():
            raise ValueError("metric_name must not be blank")
        if self.operator not in {
            ComparisonOperator.GT,
            ComparisonOperator.GTE,
            ComparisonOperator.LT,
            ComparisonOperator.LTE,
            ComparisonOperator.EQ,
            ComparisonOperator.NE,
        }:
            raise ValueError("metric rule operator must be a numeric comparison operator")
        if self.unit is not None and not self.unit.strip():
            raise ValueError("metric unit must not be blank when provided")


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    criterion_id: str
    description: str
    weight: float = 1.0
    minimum_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.criterion_id.strip():
            raise ValueError("rubric criterion_id must not be blank")
        if not self.description.strip():
            raise ValueError("rubric description must not be blank")
        if self.weight <= 0:
            raise ValueError("rubric weight must be greater than zero")
        if not 0.0 <= self.minimum_score <= 1.0:
            raise ValueError("rubric minimum_score must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One repeatable, explicitly versioned evaluation scenario."""

    case_id: str
    name: str
    version: str
    input_template: dict[str, JsonValue] = field(default_factory=dict)
    fixtures: tuple[str, ...] = ()
    assertions: tuple[DeterministicAssertion, ...] = ()
    metric_rules: tuple[MetricRule, ...] = ()
    rubric: tuple[RubricCriterion, ...] = ()
    timeout_seconds: float | None = None
    resource_limits: tuple[SnapshotValue, ...] = ()
    tags: tuple[str, ...] = ()
    category: str | None = None
    difficulty: str | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("evaluation case_id must not be blank")
        if not self.name.strip():
            raise ValueError("evaluation case name must not be blank")
        if not self.version.strip():
            raise ValueError("evaluation case version must not be blank")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("evaluation case tags must be unique")
        assertion_ids = [item.assertion_id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("evaluation assertion IDs must be unique per case")
        metric_rule_ids = [item.rule_id for item in self.metric_rules]
        if len(metric_rule_ids) != len(set(metric_rule_ids)):
            raise ValueError("evaluation metric rule IDs must be unique per case")
        rubric_ids = [item.criterion_id for item in self.rubric]
        if len(rubric_ids) != len(set(rubric_ids)):
            raise ValueError("rubric criterion IDs must be unique per case")


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    suite_id: str
    name: str
    version: str
    cases: tuple[EvaluationCase, ...]
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.suite_id.strip():
            raise ValueError("evaluation suite_id must not be blank")
        if not self.name.strip():
            raise ValueError("evaluation suite name must not be blank")
        if not self.version.strip():
            raise ValueError("evaluation suite version must not be blank")
        identities = [(case.case_id, case.version) for case in self.cases]
        if len(identities) != len(set(identities)):
            raise ValueError("evaluation suite case identities must be unique")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("evaluation suite tags must be unique")


@dataclass(frozen=True, slots=True)
class EvaluatorDescriptor:
    evaluator_id: str
    kind: EvaluatorKind
    version: str
    deterministic: bool
    model_config_id: str | None = None
    provider_id: str | None = None
    configuration_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.evaluator_id.strip():
            raise ValueError("evaluator_id must not be blank")
        if not self.version.strip():
            raise ValueError("evaluator version must not be blank")
        if self.kind is EvaluatorKind.MODEL_JUDGE:
            if self.deterministic:
                raise ValueError("model-based evaluator cannot claim deterministic behavior")
            if self.model_config_id is None or not self.model_config_id.strip():
                raise ValueError("model-based evaluator must record model_config_id")
            if self.provider_id is None or not self.provider_id.strip():
                raise ValueError("model-based evaluator must record provider_id")


@dataclass(frozen=True, slots=True)
class EvaluationAttempt:
    """Stable identity and reproducibility metadata for one case repetition."""

    evaluation_run_id: str
    case_id: str
    case_version: str
    repetition_index: int
    seed: int | None = None
    attempt_id: str = field(default_factory=lambda: new_id("evaluation_attempt"))

    def __post_init__(self) -> None:
        if not self.evaluation_run_id.strip():
            raise ValueError("evaluation attempt run ID must not be blank")
        if not self.case_id.strip():
            raise ValueError("evaluation attempt case ID must not be blank")
        if not self.case_version.strip():
            raise ValueError("evaluation attempt case version must not be blank")
        if self.repetition_index < 0:
            raise ValueError("evaluation repetition_index must be >= 0")


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    """Backend-neutral evidence presented to evaluators."""

    data: dict[str, JsonValue] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    task_id: str | None = None
    run_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    telemetry_refs: tuple[str, ...] = ()
    selected_model_config_id: str | None = None
    selected_provider_id: str | None = None
    capability_refs: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssertionResult:
    assertion_id: str
    passed: bool
    message: str
    expected: JsonValue = None
    actual: JsonValue = None


@dataclass(frozen=True, slots=True)
class MetricResult:
    metric_name: str
    value: float
    passed: bool | None = None
    threshold: float | None = None
    operator: ComparisonOperator | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    evaluation_run_id: str
    case_id: str
    case_version: str
    evaluator: EvaluatorDescriptor
    outcome: EvaluationOutcome
    deterministic_pass: bool | None = None
    score: float | None = None
    assertions: tuple[AssertionResult, ...] = ()
    metrics: tuple[MetricResult, ...] = ()
    case_tags: tuple[str, ...] = ()
    task_id: str | None = None
    run_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    telemetry_refs: tuple[str, ...] = ()
    attempt_id: str | None = None
    repetition_index: int = 0
    seed: int | None = None
    error_category: str | None = None
    error_message: str | None = None
    result_id: str = field(default_factory=lambda: new_id("evaluation_result"))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.evaluation_run_id.strip():
            raise ValueError("evaluation_run_id must not be blank")
        if not self.case_id.strip():
            raise ValueError("case_id must not be blank")
        if not self.case_version.strip():
            raise ValueError("case_version must not be blank")
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise ValueError("evaluation score must be between 0.0 and 1.0")
        if self.attempt_id is not None and not self.attempt_id.strip():
            raise ValueError("attempt_id must not be blank when provided")
        if self.repetition_index < 0:
            raise ValueError("evaluation result repetition_index must be >= 0")
        if self.outcome is EvaluationOutcome.ERROR and self.error_category is None:
            raise ValueError("error evaluation results must include error_category")


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    suite_id: str
    suite_version: str
    snapshot: ConfigurationSnapshot
    status: EvaluationRunStatus = EvaluationRunStatus.PENDING
    baseline_run_id: str | None = None
    repetitions: int = 1
    seed: int | None = None
    run_id: str = field(default_factory=lambda: new_id("evaluation_run"))
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.suite_id.strip():
            raise ValueError("evaluation run suite_id must not be blank")
        if not self.suite_version.strip():
            raise ValueError("evaluation run suite_version must not be blank")
        if self.baseline_run_id is not None and not self.baseline_run_id.strip():
            raise ValueError("baseline_run_id must not be blank when provided")
        if self.repetitions <= 0:
            raise ValueError("evaluation run repetitions must be greater than zero")
        if self.status is EvaluationRunStatus.COMPLETED and self.completed_at is None:
            raise ValueError("completed evaluation runs must include completed_at")


@dataclass(frozen=True, slots=True)
class RegressionRule:
    rule_id: str
    kind: RegressionRuleKind
    threshold: float | None = None
    metric_name: str | None = None
    metric_operator: ComparisonOperator | None = None
    tag: str | None = None

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("regression rule_id must not be blank")
        if self.kind is RegressionRuleKind.SCORE_DROP:
            if self.threshold is None or self.threshold < 0:
                raise ValueError("score-drop rules require a non-negative threshold")
        if self.kind is RegressionRuleKind.TAGGED_CASE_FAILURE:
            if self.tag is None or not self.tag.strip():
                raise ValueError("tagged-case rules require a non-blank tag")
        if self.kind is RegressionRuleKind.METRIC_THRESHOLD:
            if self.metric_name is None or not self.metric_name.strip():
                raise ValueError("metric-threshold rules require metric_name")
            if self.threshold is None:
                raise ValueError("metric-threshold rules require threshold")
            if self.metric_operator not in {
                ComparisonOperator.GT,
                ComparisonOperator.GTE,
                ComparisonOperator.LT,
                ComparisonOperator.LTE,
                ComparisonOperator.EQ,
                ComparisonOperator.NE,
            }:
                raise ValueError("metric-threshold rules require a numeric operator")


@dataclass(frozen=True, slots=True)
class RegressionPolicy:
    policy_id: str
    version: str
    rules: tuple[RegressionRule, ...]

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("regression policy_id must not be blank")
        if not self.version.strip():
            raise ValueError("regression policy version must not be blank")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("regression policy rule IDs must be unique")


@dataclass(frozen=True, slots=True)
class ComparisonFinding:
    kind: ComparisonKind
    rule_id: str
    case_id: str
    message: str
    baseline_result_id: str | None
    current_result_id: str


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    baseline_run_id: str
    current_run_id: str
    policy_id: str
    policy_version: str
    findings: tuple[ComparisonFinding, ...]

    @property
    def regressions(self) -> tuple[ComparisonFinding, ...]:
        return tuple(item for item in self.findings if item.kind is ComparisonKind.REGRESSION)

    @property
    def improvements(self) -> tuple[ComparisonFinding, ...]:
        return tuple(item for item in self.findings if item.kind is ComparisonKind.IMPROVEMENT)
