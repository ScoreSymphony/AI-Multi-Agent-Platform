from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.evaluation.context import EvaluationExecutionContext
from ai_multi_agent_platform.evaluation.evaluators import DeterministicAssertionEvaluator
from ai_multi_agent_platform.evaluation.manifest_repository import SqliteEvalManifestRepository
from ai_multi_agent_platform.evaluation.models import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationRun,
    EvaluationSuite,
    SnapshotValue,
    VersionReference,
)
from ai_multi_agent_platform.evaluation.repository import InMemoryEvaluationRepository
from ai_multi_agent_platform.evaluation.reproducibility import (
    Comparability,
    EvalManifestBuilder,
    EvalManifestContext,
    ManifestComparator,
    ManifestReference,
    RandomnessMode,
    RepeatPolicy,
    RepeatStrategy,
    SeedPolicy,
    decode_manifest,
    encode_manifest,
    manifest_projection,
)
from ai_multi_agent_platform.evaluation.runner import EvaluationRunner
from ai_multi_agent_platform.evaluation.sqlite_repository import SqliteEvaluationRepository


class StaticExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, execution_context
        return EvaluationObservation(
            data={"result": {"status": "ok"}},
            metrics={"repetition": float(attempt.repetition_index)},
        )


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="issue-594.hardening.case",
        name="Issue 594 hardening case",
        version="1",
        assertions=(
            DeterministicAssertion(
                assertion_id="status-ok",
                path="result.status",
                operator=ComparisonOperator.EQ,
                expected="ok",
            ),
        ),
    )


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="issue-594.hardening",
        name="Issue 594 hardening",
        version="1",
        cases=(_case(),),
    )


def _snapshot(
    *,
    commit: str = "abc123",
    extra: tuple[VersionReference, ...] = (),
) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        platform_version="1.0.0",
        platform_commit=commit,
        references=(VersionReference("model", "local", "1"), *extra),
        environment=(SnapshotValue("python", "3.12"),),
    )


def _run(run_id: str, *, snapshot: ConfigurationSnapshot | None = None) -> EvaluationRun:
    return EvaluationRun(
        suite_id=_suite().suite_id,
        suite_version=_suite().version,
        snapshot=snapshot or _snapshot(),
        run_id=run_id,
    )


def test_schema_10_manifest_remains_decodable_after_schema_11_digest_change() -> None:
    current = EvalManifestBuilder().build(
        run=_run("legacy-run"),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )
    legacy = replace(
        current,
        schema_version="1.0",
        manifest_digest="",
        manifest_id="",
    )

    restored = decode_manifest(encode_manifest(legacy))

    assert restored == legacy
    assert restored.schema_version == "1.0"
    assert restored.manifest_id.startswith("eval_manifest_")
    assert len(restored.manifest_id) == len("eval_manifest_") + 24


def test_context_and_source_drift_are_independently_blocking() -> None:
    builder = EvalManifestBuilder()
    seed_policy = SeedPolicy(RandomnessMode.DETERMINISTIC)
    baseline = builder.build(
        run=_run("baseline"),
        suite=_suite(),
        seed_policy=seed_policy,
        context=EvalManifestContext(
            context_bundles=(ManifestReference("context_bundle", "ctx", digest="ctx-a"),),
            fixture_sources=(ManifestReference("source", "repo", revision="source-a"),),
        ),
    )
    context_drift = builder.build(
        run=_run("context-drift"),
        suite=_suite(),
        seed_policy=seed_policy,
        context=EvalManifestContext(
            context_bundles=(ManifestReference("context_bundle", "ctx", digest="ctx-b"),),
            fixture_sources=(ManifestReference("source", "repo", revision="source-a"),),
        ),
    )
    source_drift = builder.build(
        run=_run("source-drift"),
        suite=_suite(),
        seed_policy=seed_policy,
        context=EvalManifestContext(
            context_bundles=(ManifestReference("context_bundle", "ctx", digest="ctx-a"),),
            fixture_sources=(ManifestReference("source", "repo", revision="source-b"),),
        ),
    )

    context_comparison = ManifestComparator().compare(baseline, context_drift)
    source_comparison = ManifestComparator().compare(baseline, source_drift)

    assert context_comparison.status is Comparability.INCOMPARABLE
    assert any(
        item.path.startswith("context_bundles") for item in context_comparison.blocking_differences
    )
    assert source_comparison.status is Comparability.INCOMPARABLE
    assert any(
        item.path.startswith("fixture_sources") for item in source_comparison.blocking_differences
    )


def test_comparison_lens_refs_do_not_invalidate_execution_comparability() -> None:
    builder = EvalManifestBuilder()
    seed_policy = SeedPolicy(RandomnessMode.DETERMINISTIC)
    baseline = builder.build(
        run=_run("baseline-lens"),
        suite=_suite(),
        seed_policy=seed_policy,
    )
    candidate = builder.build(
        run=_run(
            "candidate-lens",
            snapshot=_snapshot(
                extra=(VersionReference("regression_policy", "policy", "1"),),
            ),
        ),
        suite=_suite(),
        seed_policy=seed_policy,
    )

    comparison = ManifestComparator().compare(baseline, candidate)

    assert comparison.status is Comparability.WARNING
    assert comparison.blocking_differences == ()


def test_explicit_platform_candidate_allows_platform_commit_change() -> None:
    builder = EvalManifestBuilder()
    seed_policy = SeedPolicy(RandomnessMode.DETERMINISTIC)
    baseline = builder.build(
        run=_run("platform-a"),
        suite=_suite(),
        seed_policy=seed_policy,
    )
    candidate = builder.build(
        run=_run("platform-b", snapshot=_snapshot(commit="def456")),
        suite=_suite(),
        seed_policy=seed_policy,
    )

    blocked = ManifestComparator().compare(baseline, candidate)
    intentional = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({"platform"}),
    )

    assert blocked.status is Comparability.INCOMPARABLE
    assert intentional.status is Comparability.DIRECT


def test_stability_repeat_policy_stops_early_and_reports_statistics() -> None:
    async def scenario() -> None:
        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
            executor=StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        summary = await runner.run_suite(
            suite=_suite(),
            snapshot=_snapshot(),
            repetitions=5,
            repeat_policy=RepeatPolicy(
                strategy=RepeatStrategy.STABILITY,
                repeat_count=5,
                min_repeats=2,
                stability_window=2,
                variance_threshold=0.0,
            ),
            seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
        )

        assert summary.run.repetitions == 2
        assert len(summary.results) == 2
        assert summary.manifest is not None
        projection = manifest_projection(summary.manifest, results=summary.results)
        assert projection["actual_repeat_count"] == 2
        assert projection["repeat_completion"] == "stability_reached"
        statistics = projection["repeat_statistics"]
        assert isinstance(statistics, list)
        assert statistics[0]["sample_count"] == 2
        assert statistics[0]["score_variance"] == 0.0

    asyncio.run(scenario())


def test_paired_ab_allows_only_declared_candidate_dimension() -> None:
    builder = EvalManifestBuilder()
    repeat_policy = RepeatPolicy(RepeatStrategy.PAIRED_AB, 3)
    seed_policy = SeedPolicy(
        RandomnessMode.FIXED_SEED_SUPPORTED,
        ordered_seeds=(11, 17, 23),
        provider_seed_control=True,
    )
    baseline = builder.build(
        run=replace(_run("paired-a"), repetitions=3),
        suite=_suite(),
        repeat_policy=repeat_policy,
        seed_policy=seed_policy,
        context=EvalManifestContext(
            skill_bundles=(ManifestReference("skill_bundle", "candidate", digest="skill-a"),),
        ),
    )
    candidate = builder.build(
        run=replace(_run("paired-b"), repetitions=3),
        suite=_suite(),
        repeat_policy=repeat_policy,
        seed_policy=seed_policy,
        context=EvalManifestContext(
            skill_bundles=(ManifestReference("skill_bundle", "candidate", digest="skill-b"),),
        ),
    )

    comparison = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({"skill_bundle"}),
    )

    assert comparison.status is Comparability.DIRECT


def test_repeat_results_and_manifest_survive_sqlite_restart(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "evaluation.db"
        repository = SqliteEvaluationRepository(database)
        manifests = SqliteEvalManifestRepository(database)
        runner = EvaluationRunner(
            repository=repository,
            manifest_repository=manifests,
            executor=StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        summary = await runner.run_suite(
            suite=_suite(),
            snapshot=_snapshot(),
            repetitions=3,
            repeat_policy=RepeatPolicy(RepeatStrategy.FIXED_N, 3),
            seed_policy=SeedPolicy(
                RandomnessMode.FIXED_SEED_SUPPORTED,
                ordered_seeds=(3, 5, 7),
                provider_seed_control=True,
            ),
        )

        restored_repository = SqliteEvaluationRepository(database)
        restored_manifests = SqliteEvalManifestRepository(database)
        restored_results = restored_repository.list_results(summary.run.run_id)
        restored_manifest = restored_manifests.get_manifest(summary.run.run_id)

        assert len(restored_results) == 3
        assert [item.repetition_index for item in restored_results] == [0, 1, 2]
        assert [item.seed for item in restored_results] == [3, 5, 7]
        assert restored_manifest == summary.manifest

    asyncio.run(scenario())
