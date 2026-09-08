"""Canonical reproducibility evidence for the existing evaluation framework (#594)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from statistics import mean, pvariance
from typing import Any, cast

from .aggregation import ComparableEvaluationResult
from .models import (
    ComparisonReport,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    RegressionPolicy,
    SnapshotValue,
    VersionReference,
)
from .regression import RegressionEngine

EVAL_MANIFEST_SCHEMA_VERSION = "1.1"
_LEGACY_EVAL_MANIFEST_SCHEMA_VERSION = "1.0"
_COMPARISON_LENS_REFERENCE_KINDS = frozenset({"aggregation_policy", "regression_policy"})
_PRIVATE_ID_TOKENS = (
    "session_id",
    "session-id",
    "conversation_id",
    "conversation-id",
    "thread_id",
    "thread-id",
    "provider_session",
    "provider-session",
)


class RepeatStrategy(StrEnum):
    SINGLE = "single"
    FIXED_N = "fixed_n"
    STABILITY = "stability"
    PAIRED_AB = "paired_ab"


class RandomnessMode(StrEnum):
    DETERMINISTIC = "deterministic_no_randomness"
    FIXED_SEED_SUPPORTED = "fixed_seed_supported"
    FIXED_SEED_UNSUPPORTED = "fixed_seed_requested_provider_unsupported"
    PROVIDER_MANAGED = "provider_managed_stochastic"
    EXTERNAL_NONDETERMINISM = "external_nondeterminism_present"
    UNKNOWN = "seed_support_unknown"


class Comparability(StrEnum):
    DIRECT = "directly_comparable"
    WARNING = "comparable_with_warnings"
    INCOMPARABLE = "incomparable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepeatPolicy:
    strategy: RepeatStrategy
    repeat_count: int
    min_repeats: int = 1
    stability_window: int | None = None
    variance_threshold: float | None = None
    version: str = "1.0"

    def __post_init__(self) -> None:
        if self.repeat_count <= 0:
            raise ValueError("repeat_count must be greater than zero")
        if self.min_repeats <= 0 or self.min_repeats > self.repeat_count:
            raise ValueError("min_repeats must be between one and repeat_count")
        if not self.version.strip():
            raise ValueError("repeat policy version must not be blank")
        if self.strategy is RepeatStrategy.SINGLE and self.repeat_count != 1:
            raise ValueError("single repeat strategy requires repeat_count=1")
        if self.strategy is RepeatStrategy.STABILITY:
            if self.stability_window is None or self.stability_window <= 0:
                raise ValueError("stability strategy requires a positive stability_window")
            if self.stability_window > self.repeat_count:
                raise ValueError("stability_window cannot exceed repeat_count")
            if self.variance_threshold is None or self.variance_threshold < 0:
                raise ValueError("stability strategy requires a non-negative variance_threshold")
        elif self.stability_window is not None or self.variance_threshold is not None:
            raise ValueError("stability fields are only valid with strategy=stability")

    @classmethod
    def for_run(cls, run: EvaluationRun) -> "RepeatPolicy":
        strategy = RepeatStrategy.SINGLE if run.repetitions == 1 else RepeatStrategy.FIXED_N
        return cls(strategy=strategy, repeat_count=run.repetitions)


@dataclass(frozen=True, slots=True)
class SeedPolicy:
    mode: RandomnessMode
    ordered_seeds: tuple[int, ...] = ()
    provider_seed_control: bool | None = None
    limitations: tuple[str, ...] = ()
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("seed policy version must not be blank")
        if self.mode is RandomnessMode.DETERMINISTIC and self.ordered_seeds:
            raise ValueError("deterministic seed policy cannot record seeds")
        if self.mode is RandomnessMode.FIXED_SEED_SUPPORTED:
            if not self.ordered_seeds:
                raise ValueError("fixed-seed-supported policy requires ordered seeds")
            if self.provider_seed_control is not True:
                raise ValueError("fixed-seed-supported requires provider_seed_control=True")
        if self.mode is RandomnessMode.FIXED_SEED_UNSUPPORTED:
            if not self.ordered_seeds:
                raise ValueError("unsupported fixed-seed policy must preserve requested seeds")
            if self.provider_seed_control is not False:
                raise ValueError("unsupported fixed-seed requires provider_seed_control=False")
        if len(self.limitations) != len(set(self.limitations)):
            raise ValueError("seed policy limitations must be unique")

    @classmethod
    def conservative_for_run(cls, run: EvaluationRun) -> "SeedPolicy":
        if run.seed is None:
            return cls(
                mode=RandomnessMode.UNKNOWN,
                limitations=("provider seed support was not declared",),
            )
        return cls(
            mode=RandomnessMode.UNKNOWN,
            ordered_seeds=tuple(run.seed + index for index in range(run.repetitions)),
            limitations=("legacy seed values do not prove provider seed support",),
        )


@dataclass(frozen=True, slots=True)
class ManifestReference:
    kind: str
    ref_id: str
    version: str | None = None
    revision: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.ref_id.strip():
            raise ValueError("manifest reference identity must not be blank")
        if self.version is None and self.revision is None and self.digest is None:
            raise ValueError("manifest reference requires version, revision or digest")
        for value in (self.kind, self.ref_id, self.version, self.revision, self.digest):
            if value is None:
                continue
            if not value.strip():
                raise ValueError("manifest reference values must not be blank")
            _reject_private_identity(value)

    @classmethod
    def from_version_reference(cls, value: VersionReference) -> "ManifestReference":
        return cls(
            kind=value.kind,
            ref_id=value.ref_id,
            version=value.version,
            revision=value.revision,
        )


@dataclass(frozen=True, slots=True)
class CaseReproducibilitySpec:
    case_id: str
    case_version: str
    fixtures: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    resource_limits: tuple[SnapshotValue, ...] = ()


@dataclass(frozen=True, slots=True)
class EnvironmentFingerprint:
    values: tuple[SnapshotValue, ...] = ()

    def __post_init__(self) -> None:
        keys = [item.key for item in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("environment fingerprint keys must be unique")
        for item in self.values:
            _reject_private_identity(item.key)

    @property
    def digest(self) -> str:
        return _sha256(_snapshot_payload(self.values))


@dataclass(frozen=True, slots=True)
class EvalManifestContext:
    skill_bundles: tuple[ManifestReference, ...] = ()
    context_bundles: tuple[ManifestReference, ...] = ()
    research_evidence: tuple[ManifestReference, ...] = ()
    fixture_sources: tuple[ManifestReference, ...] = ()
    verification_policies: tuple[ManifestReference, ...] = ()
    dependencies: tuple[ManifestReference, ...] = ()
    contract_versions: tuple[ManifestReference, ...] = ()
    workspace: ManifestReference | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalManifest:
    evaluation_run_id: str
    suite_id: str
    suite_version: str
    cases: tuple[CaseReproducibilitySpec, ...]
    platform_version: str
    platform_commit: str | None
    configuration_references: tuple[ManifestReference, ...]
    repeat_policy: RepeatPolicy
    seed_policy: SeedPolicy
    environment: EnvironmentFingerprint
    skill_bundles: tuple[ManifestReference, ...] = ()
    context_bundles: tuple[ManifestReference, ...] = ()
    research_evidence: tuple[ManifestReference, ...] = ()
    fixture_sources: tuple[ManifestReference, ...] = ()
    verification_policies: tuple[ManifestReference, ...] = ()
    dependencies: tuple[ManifestReference, ...] = ()
    contract_versions: tuple[ManifestReference, ...] = ()
    workspace: ManifestReference | None = None
    limitations: tuple[str, ...] = ()
    schema_version: str = EVAL_MANIFEST_SCHEMA_VERSION
    manifest_digest: str = ""
    manifest_id: str = ""

    def __post_init__(self) -> None:
        if not self.evaluation_run_id.strip():
            raise ValueError("manifest evaluation_run_id must not be blank")
        if not self.suite_id.strip() or not self.suite_version.strip():
            raise ValueError("manifest suite identity/version must not be blank")
        if not self.platform_version.strip() or not self.schema_version.strip():
            raise ValueError("manifest platform/schema version must not be blank")
        if self.schema_version == _LEGACY_EVAL_MANIFEST_SCHEMA_VERSION:
            digest = _sha256(self.canonical_payload())
            manifest_id = f"eval_manifest_{digest[:24]}"
        elif self.schema_version == EVAL_MANIFEST_SCHEMA_VERSION:
            digest = _sha256(self.reproducibility_payload())
            run_hash = hashlib.sha256(self.evaluation_run_id.encode()).hexdigest()[:12]
            manifest_id = f"eval_manifest_{run_hash}_{digest[:12]}"
        else:
            raise ValueError(f"unsupported EvalManifest schema version: {self.schema_version}")
        if self.manifest_digest and self.manifest_digest != digest:
            raise ValueError("manifest_digest does not match manifest schema payload")
        if self.manifest_id and self.manifest_id != manifest_id:
            raise ValueError("manifest_id does not match manifest schema identity")
        object.__setattr__(self, "manifest_digest", digest)
        object.__setattr__(self, "manifest_id", manifest_id)

    def reproducibility_payload(self) -> dict[str, object]:
        """Run-independent effective configuration used by schema 1.1 digests."""
        return {
            "schema_version": self.schema_version,
            "suite": {"id": self.suite_id, "version": self.suite_version},
            "cases": [_case_payload(case) for case in self.cases],
            "platform": {"version": self.platform_version, "commit": self.platform_commit},
            "configuration_references": _refs_payload(self.configuration_references),
            "repeat_policy": _repeat_payload(self.repeat_policy),
            "seed_policy": _seed_payload(self.seed_policy),
            "environment": {
                "digest": self.environment.digest,
                "values": _snapshot_payload(self.environment.values),
            },
            "skill_bundles": _refs_payload(self.skill_bundles),
            "context_bundles": _refs_payload(self.context_bundles),
            "research_evidence": _refs_payload(self.research_evidence),
            "fixture_sources": _refs_payload(self.fixture_sources),
            "verification_policies": _refs_payload(self.verification_policies),
            "dependencies": _refs_payload(self.dependencies),
            "contract_versions": _refs_payload(self.contract_versions),
            "workspace": None if self.workspace is None else _ref_payload(self.workspace),
            "limitations": list(self.limitations),
        }

    def canonical_payload(self) -> dict[str, object]:
        payload = self.reproducibility_payload()
        payload["evaluation_run_id"] = self.evaluation_run_id
        return payload

    def to_payload(self) -> dict[str, object]:
        payload = self.canonical_payload()
        payload["manifest_id"] = self.manifest_id
        payload["manifest_digest"] = self.manifest_digest
        return payload


class EvalManifestBuilder:
    def build(
        self,
        *,
        run: EvaluationRun,
        suite: EvaluationSuite,
        repeat_policy: RepeatPolicy | None = None,
        seed_policy: SeedPolicy | None = None,
        context: EvalManifestContext | None = None,
    ) -> EvalManifest:
        if (run.suite_id, run.suite_version) != (suite.suite_id, suite.version):
            raise ValueError("evaluation run and suite identity/version do not match")
        repeats = repeat_policy or RepeatPolicy.for_run(run)
        if repeats.repeat_count != run.repetitions:
            raise ValueError("repeat policy repeat_count must match EvaluationRun.repetitions")
        randomness = seed_policy or SeedPolicy.conservative_for_run(run)
        if randomness.mode is RandomnessMode.FIXED_SEED_SUPPORTED:
            if len(randomness.ordered_seeds) != run.repetitions:
                raise ValueError("fixed supported seed set must contain one seed per repetition")
        extras = context or EvalManifestContext()
        return EvalManifest(
            evaluation_run_id=run.run_id,
            suite_id=suite.suite_id,
            suite_version=suite.version,
            cases=tuple(
                CaseReproducibilitySpec(
                    case_id=case.case_id,
                    case_version=case.version,
                    fixtures=case.fixtures,
                    timeout_seconds=case.timeout_seconds,
                    resource_limits=_sorted_snapshot(case.resource_limits),
                )
                for case in suite.cases
            ),
            platform_version=run.snapshot.platform_version,
            platform_commit=run.snapshot.platform_commit,
            configuration_references=_sorted_refs(
                tuple(
                    ManifestReference.from_version_reference(item)
                    for item in run.snapshot.references
                )
            ),
            repeat_policy=repeats,
            seed_policy=randomness,
            environment=EnvironmentFingerprint(_sorted_snapshot(run.snapshot.environment)),
            skill_bundles=_sorted_refs(extras.skill_bundles),
            context_bundles=_sorted_refs(extras.context_bundles),
            research_evidence=_sorted_refs(extras.research_evidence),
            fixture_sources=_sorted_refs(extras.fixture_sources),
            verification_policies=_sorted_refs(extras.verification_policies),
            dependencies=_sorted_refs(extras.dependencies),
            contract_versions=_sorted_refs(extras.contract_versions),
            workspace=extras.workspace,
            limitations=extras.limitations,
        )


@dataclass(frozen=True, slots=True)
class ManifestDifference:
    path: str
    baseline: object
    candidate: object
    blocking: bool
    intentional_candidate_dimension: bool = False


@dataclass(frozen=True, slots=True)
class ManifestComparison:
    status: Comparability
    differences: tuple[ManifestDifference, ...]

    @property
    def blocking_differences(self) -> tuple[ManifestDifference, ...]:
        return tuple(item for item in self.differences if item.blocking)


class ManifestComparator:
    def compare(
        self,
        baseline: EvalManifest | None,
        candidate: EvalManifest | None,
        *,
        candidate_reference_kinds: frozenset[str] = frozenset(),
        performance_sensitive: bool = False,
    ) -> ManifestComparison:
        if baseline is None or candidate is None:
            return ManifestComparison(Comparability.UNKNOWN, ())
        differences: list[ManifestDifference] = []
        self._value(
            differences,
            "suite",
            (baseline.suite_id, baseline.suite_version),
            (candidate.suite_id, candidate.suite_version),
        )
        self._value(differences, "cases", baseline.cases, candidate.cases)
        platform_candidate = "platform" in candidate_reference_kinds
        self._value(
            differences,
            "platform.version",
            baseline.platform_version,
            candidate.platform_version,
            intentional=platform_candidate,
        )
        self._value(
            differences,
            "platform.commit",
            baseline.platform_commit,
            candidate.platform_commit,
            intentional=platform_candidate,
        )
        ref_groups = (
            (
                "configuration_references",
                baseline.configuration_references,
                candidate.configuration_references,
            ),
            ("skill_bundles", baseline.skill_bundles, candidate.skill_bundles),
            ("context_bundles", baseline.context_bundles, candidate.context_bundles),
            ("research_evidence", baseline.research_evidence, candidate.research_evidence),
            ("fixture_sources", baseline.fixture_sources, candidate.fixture_sources),
            (
                "verification_policies",
                baseline.verification_policies,
                candidate.verification_policies,
            ),
            ("dependencies", baseline.dependencies, candidate.dependencies),
            ("contract_versions", baseline.contract_versions, candidate.contract_versions),
        )
        for path, left, right in ref_groups:
            self._refs(
                differences,
                path,
                left,
                right,
                candidate_reference_kinds=candidate_reference_kinds,
            )
        self._value(differences, "workspace", baseline.workspace, candidate.workspace)
        self._value(
            differences,
            "repeat_policy",
            _repeat_payload(baseline.repeat_policy),
            _repeat_payload(candidate.repeat_policy),
        )
        if _seed_payload(baseline.seed_policy) != _seed_payload(candidate.seed_policy):
            provider_managed = (
                baseline.seed_policy.mode is RandomnessMode.PROVIDER_MANAGED
                and candidate.seed_policy.mode is RandomnessMode.PROVIDER_MANAGED
            )
            differences.append(
                ManifestDifference(
                    "seed_policy",
                    _seed_payload(baseline.seed_policy),
                    _seed_payload(candidate.seed_policy),
                    not provider_managed,
                )
            )
        left_env = {item.key: item.value for item in baseline.environment.values}
        right_env = {item.key: item.value for item in candidate.environment.values}
        for key in sorted(left_env.keys() | right_env.keys()):
            if left_env.get(key) == right_env.get(key):
                continue
            resource_key = any(
                token in key.lower()
                for token in ("cpu", "gpu", "ram", "memory", "hardware", "resource")
            )
            differences.append(
                ManifestDifference(
                    path=f"environment.{key}",
                    baseline=left_env.get(key),
                    candidate=right_env.get(key),
                    blocking=performance_sensitive and resource_key,
                )
            )
        if baseline.limitations != candidate.limitations:
            differences.append(
                ManifestDifference(
                    path="limitations",
                    baseline=baseline.limitations,
                    candidate=candidate.limitations,
                    blocking=False,
                )
            )
        if any(item.blocking for item in differences):
            status = Comparability.INCOMPARABLE
        elif any(not item.intentional_candidate_dimension for item in differences):
            status = Comparability.WARNING
        else:
            status = Comparability.DIRECT
        return ManifestComparison(status, tuple(differences))

    @staticmethod
    def _value(
        differences: list[ManifestDifference],
        path: str,
        baseline: object,
        candidate: object,
        *,
        intentional: bool = False,
    ) -> None:
        if baseline != candidate:
            differences.append(
                ManifestDifference(
                    path,
                    baseline,
                    candidate,
                    blocking=not intentional,
                    intentional_candidate_dimension=intentional,
                )
            )

    @staticmethod
    def _refs(
        differences: list[ManifestDifference],
        path: str,
        baseline: tuple[ManifestReference, ...],
        candidate: tuple[ManifestReference, ...],
        *,
        candidate_reference_kinds: frozenset[str],
    ) -> None:
        left = {(item.kind, item.ref_id): _ref_payload(item) for item in baseline}
        right = {(item.kind, item.ref_id): _ref_payload(item) for item in candidate}
        for identity in sorted(left.keys() | right.keys()):
            if left.get(identity) == right.get(identity):
                continue
            kind, ref_id = identity
            intentional = kind in candidate_reference_kinds
            comparison_lens = kind in _COMPARISON_LENS_REFERENCE_KINDS
            differences.append(
                ManifestDifference(
                    path=f"{path}.{kind}:{ref_id}",
                    baseline=left.get(identity),
                    candidate=right.get(identity),
                    blocking=not intentional and not comparison_lens,
                    intentional_candidate_dimension=intentional,
                )
            )


class ReproducibleRegressionGate:
    def __init__(
        self,
        *,
        comparator: ManifestComparator | None = None,
        regression_engine: RegressionEngine | None = None,
    ) -> None:
        self._comparator = comparator or ManifestComparator()
        self._regression_engine = regression_engine or RegressionEngine()

    def compare(
        self,
        *,
        baseline_manifest: EvalManifest | None,
        candidate_manifest: EvalManifest | None,
        baseline_results: tuple[ComparableEvaluationResult, ...],
        candidate_results: tuple[ComparableEvaluationResult, ...],
        policy: RegressionPolicy,
        candidate_reference_kinds: frozenset[str] = frozenset(),
        performance_sensitive: bool = False,
    ) -> tuple[ManifestComparison, ComparisonReport | None]:
        manifest_comparison = self._comparator.compare(
            baseline_manifest,
            candidate_manifest,
            candidate_reference_kinds=candidate_reference_kinds,
            performance_sensitive=performance_sensitive,
        )
        if manifest_comparison.status in {Comparability.INCOMPARABLE, Comparability.UNKNOWN}:
            return manifest_comparison, None
        assert baseline_manifest is not None
        assert candidate_manifest is not None
        report = self._regression_engine.compare(
            baseline_run_id=baseline_manifest.evaluation_run_id,
            current_run_id=candidate_manifest.evaluation_run_id,
            baseline_results=baseline_results,
            current_results=candidate_results,
            policy=policy,
        )
        return manifest_comparison, report


@dataclass(frozen=True, slots=True)
class RepeatOutcome:
    repetition_index: int
    seed: int | None
    result_ids: tuple[str, ...]
    outcomes: tuple[str, ...]
    scores: tuple[float | None, ...]


def per_repeat_outcomes(results: tuple[EvaluationResult, ...]) -> tuple[RepeatOutcome, ...]:
    grouped: dict[int, list[EvaluationResult]] = {}
    for result in results:
        grouped.setdefault(result.repetition_index, []).append(result)
    outcomes: list[RepeatOutcome] = []
    for repetition_index in sorted(grouped):
        items = sorted(grouped[repetition_index], key=lambda item: item.result_id)
        seeds = {item.seed for item in items}
        outcomes.append(
            RepeatOutcome(
                repetition_index=repetition_index,
                seed=next(iter(seeds)) if len(seeds) == 1 else None,
                result_ids=tuple(item.result_id for item in items),
                outcomes=tuple(item.outcome.value for item in items),
                scores=tuple(item.score for item in items),
            )
        )
    return tuple(outcomes)


def actual_repetition_count(results: tuple[EvaluationResult, ...]) -> int:
    if not results:
        return 0
    return max(item.repetition_index for item in results) + 1


def manifest_projection(
    manifest: EvalManifest,
    *,
    comparison: ManifestComparison | None = None,
    results: tuple[EvaluationResult, ...] = (),
) -> dict[str, object]:
    actual_repeats = actual_repetition_count(results)
    repeat_completion = "configured_repeats_completed"
    if (
        manifest.repeat_policy.strategy is RepeatStrategy.STABILITY
        and 0 < actual_repeats < manifest.repeat_policy.repeat_count
    ):
        repeat_completion = "stability_reached"
    return {
        "manifest_id": manifest.manifest_id,
        "manifest_digest": manifest.manifest_digest,
        "evaluation_run_id": manifest.evaluation_run_id,
        "schema_version": manifest.schema_version,
        "repeat_policy": _repeat_payload(manifest.repeat_policy),
        "actual_repeat_count": actual_repeats,
        "repeat_completion": repeat_completion,
        "repeat_statistics": _repeat_statistics(results),
        "seed_policy": _seed_payload(manifest.seed_policy),
        "environment": {
            "digest": manifest.environment.digest,
            "comparability": None if comparison is None else comparison.status.value,
        },
        "per_repeat_outcomes": [
            {
                "repetition_index": item.repetition_index,
                "seed": item.seed,
                "result_ids": list(item.result_ids),
                "outcomes": list(item.outcomes),
                "scores": list(item.scores),
            }
            for item in per_repeat_outcomes(results)
        ],
        "reproducibility_limitations": list(
            dict.fromkeys((*manifest.limitations, *manifest.seed_policy.limitations))
        ),
        "manifest_diff": []
        if comparison is None
        else [
            {
                "path": item.path,
                "baseline": item.baseline,
                "candidate": item.candidate,
                "blocking": item.blocking,
                "intentional_candidate_dimension": item.intentional_candidate_dimension,
            }
            for item in comparison.differences
        ],
    }


def encode_manifest(manifest: EvalManifest) -> str:
    return json.dumps(
        manifest.to_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def decode_manifest(raw: str) -> EvalManifest:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("stored EvalManifest payload must be an object")
    obj = cast(dict[str, Any], parsed)
    suite = _object(obj.get("suite"), "suite")
    platform = _object(obj.get("platform"), "platform")
    environment = _object(obj.get("environment"), "environment")
    workspace_raw = obj.get("workspace")
    manifest = EvalManifest(
        evaluation_run_id=_string(obj, "evaluation_run_id"),
        suite_id=_string(suite, "id"),
        suite_version=_string(suite, "version"),
        cases=tuple(_decode_case(_object(item, "cases[]")) for item in _list(obj, "cases")),
        platform_version=_string(platform, "version"),
        platform_commit=_optional_string(platform, "commit"),
        configuration_references=_decode_refs(obj, "configuration_references"),
        repeat_policy=_decode_repeat(_object(obj.get("repeat_policy"), "repeat_policy")),
        seed_policy=_decode_seed(_object(obj.get("seed_policy"), "seed_policy")),
        environment=EnvironmentFingerprint(
            tuple(
                SnapshotValue(key=_string(item, "key"), value=_string(item, "value"))
                for item in (
                    _object(value, "environment.values[]") for value in _list(environment, "values")
                )
            )
        ),
        skill_bundles=_decode_refs(obj, "skill_bundles"),
        context_bundles=_decode_refs(obj, "context_bundles"),
        research_evidence=_decode_refs(obj, "research_evidence"),
        fixture_sources=_decode_refs(obj, "fixture_sources"),
        verification_policies=_decode_refs(obj, "verification_policies"),
        dependencies=_decode_refs(obj, "dependencies"),
        contract_versions=_decode_refs(obj, "contract_versions"),
        workspace=(
            None if workspace_raw is None else _decode_ref(_object(workspace_raw, "workspace"))
        ),
        limitations=tuple(_strings(obj, "limitations")),
        schema_version=_string(obj, "schema_version"),
        manifest_digest=_string(obj, "manifest_digest"),
        manifest_id=_string(obj, "manifest_id"),
    )
    if manifest.environment.digest != _string(environment, "digest"):
        raise ValueError("stored environment fingerprint digest is invalid")
    return manifest


def _repeat_statistics(results: tuple[EvaluationResult, ...]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[EvaluationResult]] = {}
    for result in results:
        identity = (result.case_id, result.case_version, result.evaluator.evaluator_id)
        grouped.setdefault(identity, []).append(result)
    statistics: list[dict[str, object]] = []
    for case_id, case_version, evaluator_id in sorted(grouped):
        samples = grouped[(case_id, case_version, evaluator_id)]
        scores = [item.score for item in samples]
        numeric_scores = [float(value) for value in scores if value is not None]
        pass_count = sum(item.outcome.value == "passed" for item in samples)
        statistics.append(
            {
                "case_id": case_id,
                "case_version": case_version,
                "evaluator_id": evaluator_id,
                "sample_count": len(samples),
                "pass_rate": pass_count / len(samples),
                "score_mean": (
                    None if len(numeric_scores) != len(samples) else float(mean(numeric_scores))
                ),
                "score_variance": (
                    None
                    if len(numeric_scores) != len(samples)
                    else float(pvariance(numeric_scores))
                ),
            }
        )
    return statistics


def _sha256(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _reject_private_identity(value: str) -> None:
    normalized = value.lower()
    if any(token in normalized for token in _PRIVATE_ID_TOKENS):
        raise ValueError("provider-private session identifiers are not canonical evidence")


def _sorted_snapshot(values: tuple[SnapshotValue, ...]) -> tuple[SnapshotValue, ...]:
    return tuple(sorted(values, key=lambda item: item.key))


def _snapshot_payload(values: tuple[SnapshotValue, ...]) -> list[dict[str, str]]:
    return [{"key": item.key, "value": item.value} for item in _sorted_snapshot(values)]


def _ref_key(value: ManifestReference) -> tuple[str, str, str, str, str]:
    return (
        value.kind,
        value.ref_id,
        value.version or "",
        value.revision or "",
        value.digest or "",
    )


def _sorted_refs(values: tuple[ManifestReference, ...]) -> tuple[ManifestReference, ...]:
    return tuple(sorted(values, key=_ref_key))


def _ref_payload(value: ManifestReference) -> dict[str, object]:
    return {
        "kind": value.kind,
        "ref_id": value.ref_id,
        "version": value.version,
        "revision": value.revision,
        "digest": value.digest,
    }


def _refs_payload(values: tuple[ManifestReference, ...]) -> list[dict[str, object]]:
    return [_ref_payload(item) for item in _sorted_refs(values)]


def _case_payload(value: CaseReproducibilitySpec) -> dict[str, object]:
    return {
        "case_id": value.case_id,
        "case_version": value.case_version,
        "fixtures": list(value.fixtures),
        "timeout_seconds": value.timeout_seconds,
        "resource_limits": _snapshot_payload(value.resource_limits),
    }


def _repeat_payload(value: RepeatPolicy) -> dict[str, object]:
    return {
        "strategy": value.strategy.value,
        "repeat_count": value.repeat_count,
        "min_repeats": value.min_repeats,
        "stability_window": value.stability_window,
        "variance_threshold": value.variance_threshold,
        "version": value.version,
    }


def _seed_payload(value: SeedPolicy) -> dict[str, object]:
    return {
        "mode": value.mode.value,
        "ordered_seeds": list(value.ordered_seeds),
        "provider_seed_control": value.provider_seed_control,
        "limitations": list(value.limitations),
        "version": value.version,
    }


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"stored EvalManifest field '{field}' must be an object")
    return cast(dict[str, Any], value)


def _list(obj: dict[str, Any], key: str) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        raise ValueError(f"stored EvalManifest field '{key}' must be a list")
    return value


def _string(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stored EvalManifest field '{key}' must be a non-blank string")
    return value


def _optional_string(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stored EvalManifest field '{key}' must be null or non-blank")
    return value


def _strings(obj: dict[str, Any], key: str) -> list[str]:
    values = _list(obj, key)
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"stored EvalManifest field '{key}' must contain strings")
    return [cast(str, value) for value in values]


def _int(obj: dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored EvalManifest field '{key}' must be an integer")
    return value


def _optional_int(obj: dict[str, Any], key: str) -> int | None:
    return None if obj.get(key) is None else _int(obj, key)


def _optional_float(obj: dict[str, Any], key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"stored EvalManifest field '{key}' must be numeric or null")
    return float(value)


def _optional_bool(obj: dict[str, Any], key: str) -> bool | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"stored EvalManifest field '{key}' must be boolean or null")
    return value


def _decode_ref(obj: dict[str, Any]) -> ManifestReference:
    return ManifestReference(
        kind=_string(obj, "kind"),
        ref_id=_string(obj, "ref_id"),
        version=_optional_string(obj, "version"),
        revision=_optional_string(obj, "revision"),
        digest=_optional_string(obj, "digest"),
    )


def _decode_refs(obj: dict[str, Any], key: str) -> tuple[ManifestReference, ...]:
    return tuple(_decode_ref(_object(value, f"{key}[]")) for value in _list(obj, key))


def _decode_case(obj: dict[str, Any]) -> CaseReproducibilitySpec:
    return CaseReproducibilitySpec(
        case_id=_string(obj, "case_id"),
        case_version=_string(obj, "case_version"),
        fixtures=tuple(_strings(obj, "fixtures")),
        timeout_seconds=_optional_float(obj, "timeout_seconds"),
        resource_limits=tuple(
            SnapshotValue(key=_string(item, "key"), value=_string(item, "value"))
            for item in (
                _object(value, "resource_limits[]") for value in _list(obj, "resource_limits")
            )
        ),
    )


def _decode_repeat(obj: dict[str, Any]) -> RepeatPolicy:
    return RepeatPolicy(
        strategy=RepeatStrategy(_string(obj, "strategy")),
        repeat_count=_int(obj, "repeat_count"),
        min_repeats=_int(obj, "min_repeats"),
        stability_window=_optional_int(obj, "stability_window"),
        variance_threshold=_optional_float(obj, "variance_threshold"),
        version=_string(obj, "version"),
    )


def _decode_seed(obj: dict[str, Any]) -> SeedPolicy:
    seeds = tuple(_int({"value": value}, "value") for value in _list(obj, "ordered_seeds"))
    return SeedPolicy(
        mode=RandomnessMode(_string(obj, "mode")),
        ordered_seeds=seeds,
        provider_seed_control=_optional_bool(obj, "provider_seed_control"),
        limitations=tuple(_strings(obj, "limitations")),
        version=_string(obj, "version"),
    )
