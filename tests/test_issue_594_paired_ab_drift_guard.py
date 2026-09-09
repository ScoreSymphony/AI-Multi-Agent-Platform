from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationRun,
    EvaluationSuite,
    VersionReference,
)
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
)


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="issue-594.paired-ab-drift",
        name="Issue 594 paired A/B drift guard",
        version="1",
        cases=(EvaluationCase("paired.case", "Paired case", "1"),),
    )


def _run(run_id: str) -> EvaluationRun:
    return EvaluationRun(
        suite_id=_suite().suite_id,
        suite_version=_suite().version,
        snapshot=ConfigurationSnapshot(
            platform_version="1",
            platform_commit="commit-a",
            references=(VersionReference("model", "local", "1"),),
        ),
        run_id=run_id,
        repetitions=3,
    )


def _seed_policy(*seeds: int) -> SeedPolicy:
    return SeedPolicy(
        RandomnessMode.FIXED_SEED_SUPPORTED,
        ordered_seeds=seeds,
        provider_seed_control=True,
    )


def _context(*, skill_digest: str, source_revision: str) -> EvalManifestContext:
    return EvalManifestContext(
        skill_bundles=(ManifestReference("skill_bundle", "candidate", digest=skill_digest),),
        fixture_sources=(ManifestReference("source", "fixture", revision=source_revision),),
    )


def test_paired_ab_rejects_seed_set_drift_even_for_declared_candidate() -> None:
    builder = EvalManifestBuilder()
    repeat_policy = RepeatPolicy(RepeatStrategy.PAIRED_AB, 3)
    baseline = builder.build(
        run=replace(_run("paired-seed-a"), repetitions=3),
        suite=_suite(),
        repeat_policy=repeat_policy,
        seed_policy=_seed_policy(11, 17, 23),
        context=_context(skill_digest="skill-a", source_revision="source-a"),
    )
    candidate = builder.build(
        run=replace(_run("paired-seed-b"), repetitions=3),
        suite=_suite(),
        repeat_policy=repeat_policy,
        seed_policy=_seed_policy(11, 17, 29),
        context=_context(skill_digest="skill-b", source_revision="source-a"),
    )

    comparison = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({"skill_bundle"}),
    )

    assert comparison.status is Comparability.INCOMPARABLE
    assert comparison.blocking_differences
    assert any(item.path.startswith("seed_policy") for item in comparison.blocking_differences)


def test_paired_ab_rejects_fixture_drift_even_for_declared_candidate() -> None:
    builder = EvalManifestBuilder()
    repeat_policy = RepeatPolicy(RepeatStrategy.PAIRED_AB, 3)
    seed_policy = _seed_policy(11, 17, 23)
    baseline = builder.build(
        run=replace(_run("paired-source-a"), repetitions=3),
        suite=_suite(),
        repeat_policy=repeat_policy,
        seed_policy=seed_policy,
        context=_context(skill_digest="skill-a", source_revision="source-a"),
    )
    candidate = builder.build(
        run=replace(_run("paired-source-b"), repetitions=3),
        suite=_suite(),
        repeat_policy=repeat_policy,
        seed_policy=seed_policy,
        context=_context(skill_digest="skill-b", source_revision="source-b"),
    )

    comparison = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({"skill_bundle"}),
    )

    assert comparison.status is Comparability.INCOMPARABLE
    assert comparison.blocking_differences
    assert any(item.path.startswith("fixture_sources") for item in comparison.blocking_differences)
