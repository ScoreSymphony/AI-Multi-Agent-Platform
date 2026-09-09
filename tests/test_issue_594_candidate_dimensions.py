from __future__ import annotations

import pytest

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
    SeedPolicy,
)


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="issue-594.candidates",
        name="Issue 594 candidate dimensions",
        version="1",
        cases=(EvaluationCase("candidate.case", "Candidate case", "1"),),
    )


def _run(run_id: str, *, references: tuple[VersionReference, ...] = ()) -> EvaluationRun:
    return EvaluationRun(
        suite_id=_suite().suite_id,
        suite_version=_suite().version,
        snapshot=ConfigurationSnapshot(
            platform_version="1",
            platform_commit="commit-a",
            references=references,
        ),
        run_id=run_id,
    )


@pytest.mark.parametrize(
    ("candidate_kind", "baseline_ref", "candidate_ref"),
    (
        (
            "planner",
            VersionReference("planner", "planner-a", "1"),
            VersionReference("planner", "planner-a", "2"),
        ),
        (
            "provider",
            VersionReference("provider", "local-provider", "1"),
            VersionReference("provider", "local-provider", "2"),
        ),
    ),
)
def test_declared_reference_candidate_holds_other_dimensions_fixed(
    candidate_kind: str,
    baseline_ref: VersionReference,
    candidate_ref: VersionReference,
) -> None:
    builder = EvalManifestBuilder()
    seed_policy = SeedPolicy(RandomnessMode.DETERMINISTIC)
    baseline = builder.build(
        run=_run("baseline", references=(baseline_ref,)),
        suite=_suite(),
        seed_policy=seed_policy,
    )
    candidate = builder.build(
        run=_run("candidate", references=(candidate_ref,)),
        suite=_suite(),
        seed_policy=seed_policy,
    )

    blocked = ManifestComparator().compare(baseline, candidate)
    allowed = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({candidate_kind}),
    )

    assert blocked.status is Comparability.INCOMPARABLE
    assert allowed.status is Comparability.DIRECT
    assert allowed.differences
    assert all(item.intentional_candidate_dimension for item in allowed.differences)


def test_context_candidate_does_not_hide_unrelated_source_drift() -> None:
    builder = EvalManifestBuilder()
    seed_policy = SeedPolicy(RandomnessMode.DETERMINISTIC)
    baseline = builder.build(
        run=_run("baseline-context"),
        suite=_suite(),
        seed_policy=seed_policy,
        context=EvalManifestContext(
            context_bundles=(ManifestReference("context_bundle", "context", digest="ctx-a"),),
            fixture_sources=(ManifestReference("source", "fixture", revision="source-a"),),
        ),
    )
    candidate = builder.build(
        run=_run("candidate-context"),
        suite=_suite(),
        seed_policy=seed_policy,
        context=EvalManifestContext(
            context_bundles=(ManifestReference("context_bundle", "context", digest="ctx-b"),),
            fixture_sources=(ManifestReference("source", "fixture", revision="source-b"),),
        ),
    )

    comparison = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({"context_bundle"}),
    )

    assert comparison.status is Comparability.INCOMPARABLE
    assert any(
        item.path.startswith("context_bundles") and item.intentional_candidate_dimension
        for item in comparison.differences
    )
    assert any(
        item.path.startswith("fixture_sources") and item.blocking for item in comparison.differences
    )
