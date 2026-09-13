from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.evaluation.context import EvaluationExecutionContext
from ai_multi_agent_platform.evaluation.evaluators import DeterministicAssertionEvaluator
from ai_multi_agent_platform.evaluation.manifest_repository import (
    InMemoryEvalManifestRepository,
    SqliteEvalManifestRepository,
)
from ai_multi_agent_platform.evaluation.models import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationRun,
    EvaluationSuite,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
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
        case_id="issue-594.case",
        name="Issue 594 deterministic fixture",
        version="3",
        fixtures=("fixture://repo@abc123",),
        assertions=(
            DeterministicAssertion(
                assertion_id="status-ok",
                path="result.status",
                operator=ComparisonOperator.EQ,
                expected="ok",
            ),
        ),
        timeout_seconds=3.0,
        resource_limits=(SnapshotValue("memory_mb", "128"),),
    )


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="issue-594.suite",
        name="Issue 594 suite",
        version="7",
        cases=(_case(),),
    )


def _snapshot(*, commit: str = "abc123") -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        platform_version="0.0.1",
        platform_commit=commit,
        references=(
            VersionReference("model", "local-model", "1", revision="sha256:model"),
            VersionReference("executor", "reference", "2"),
        ),
        environment=(
            SnapshotValue("python", "3.12"),
            SnapshotValue("os", "linux"),
            SnapshotValue("cpu", "x86_64"),
        ),
    )


def _run(*, run_id: str = "evaluation_run_issue594") -> EvaluationRun:
    return EvaluationRun(
        suite_id=_suite().suite_id,
        suite_version=_suite().version,
        snapshot=_snapshot(),
        run_id=run_id,
    )


def test_manifest_digest_is_stable_and_round_trips() -> None:
    builder = EvalManifestBuilder()
    context = EvalManifestContext(
        skill_bundles=(ManifestReference("skill_bundle", "coding", digest="sha256:skill"),),
        context_bundles=(
            ManifestReference("context_bundle", "task-context", digest="sha256:context"),
        ),
        fixture_sources=(ManifestReference("source", "repo", revision="abc123"),),
    )
    policy = SeedPolicy(RandomnessMode.DETERMINISTIC)

    first = builder.build(
        run=_run(),
        suite=_suite(),
        repeat_policy=RepeatPolicy(RepeatStrategy.SINGLE, 1),
        seed_policy=policy,
        context=context,
    )
    reordered_run = replace(
        _run(),
        snapshot=ConfigurationSnapshot(
            platform_version="0.0.1",
            platform_commit="abc123",
            references=tuple(reversed(_snapshot().references)),
            environment=tuple(reversed(_snapshot().environment)),
        ),
    )
    second = builder.build(
        run=reordered_run,
        suite=_suite(),
        repeat_policy=RepeatPolicy(RepeatStrategy.SINGLE, 1),
        seed_policy=policy,
        context=context,
    )

    assert first.manifest_digest == second.manifest_digest
    assert first.manifest_id == second.manifest_id
    assert decode_manifest(encode_manifest(first)) == first


def test_fixed_seed_set_binds_each_repeat_and_projection_keeps_raw_outcomes() -> None:
    async def scenario() -> None:
        manifests = InMemoryEvalManifestRepository()
        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
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
                ordered_seeds=(11, 17, 23),
                provider_seed_control=True,
            ),
        )

        assert {(result.repetition_index, result.seed) for result in summary.results} == {
            (0, 11),
            (1, 17),
            (2, 23),
        }
        assert summary.manifest is not None
        assert summary.manifest.seed_policy.ordered_seeds == (11, 17, 23)
        projection = manifest_projection(summary.manifest, results=summary.results)
        repeats = projection["per_repeat_outcomes"]
        assert isinstance(repeats, list)
        assert [item["seed"] for item in repeats] == [11, 17, 23]

    asyncio.run(scenario())


def test_provider_without_seed_support_is_explicit_not_fabricated() -> None:
    policy = SeedPolicy(
        RandomnessMode.FIXED_SEED_UNSUPPORTED,
        ordered_seeds=(5, 6),
        provider_seed_control=False,
        limitations=("provider ignores requested seed",),
    )
    manifest = EvalManifestBuilder().build(
        run=replace(_run(), repetitions=2),
        suite=_suite(),
        repeat_policy=RepeatPolicy(RepeatStrategy.FIXED_N, 2),
        seed_policy=policy,
    )

    assert manifest.seed_policy.provider_seed_control is False
    assert manifest.seed_policy.mode is RandomnessMode.FIXED_SEED_UNSUPPORTED
    assert "provider ignores requested seed" in manifest.seed_policy.limitations


def test_skill_context_and_source_drift_are_blocking_unless_candidate_is_explicit() -> None:
    builder = EvalManifestBuilder()
    run = _run()
    base = builder.build(
        run=run,
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
        context=EvalManifestContext(
            skill_bundles=(ManifestReference("skill_bundle", "coding", digest="skill-a"),),
            context_bundles=(ManifestReference("context_bundle", "ctx", digest="ctx-a"),),
            fixture_sources=(ManifestReference("source", "repo", revision="abc123"),),
        ),
    )
    candidate = builder.build(
        run=replace(run, run_id="evaluation_run_candidate"),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
        context=EvalManifestContext(
            skill_bundles=(ManifestReference("skill_bundle", "coding", digest="skill-b"),),
            context_bundles=(ManifestReference("context_bundle", "ctx", digest="ctx-a"),),
            fixture_sources=(ManifestReference("source", "repo", revision="abc123"),),
        ),
    )
    comparator = ManifestComparator()

    blocked = comparator.compare(base, candidate)
    intentional = comparator.compare(
        base,
        candidate,
        candidate_reference_kinds=frozenset({"skill_bundle"}),
    )

    assert blocked.status is Comparability.INCOMPARABLE
    assert any(item.path.startswith("skill_bundles") for item in blocked.blocking_differences)
    assert intentional.status is Comparability.DIRECT


def test_environment_comparability_distinguishes_warning_and_performance_blocker() -> None:
    builder = EvalManifestBuilder()
    base = builder.build(
        run=_run(),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )
    changed = builder.build(
        run=replace(
            _run(run_id="evaluation_run_env"),
            snapshot=ConfigurationSnapshot(
                platform_version="0.0.1",
                platform_commit="abc123",
                references=_snapshot().references,
                environment=(
                    SnapshotValue("python", "3.12"),
                    SnapshotValue("os", "linux-next"),
                    SnapshotValue("cpu", "arm64"),
                ),
            ),
        ),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )

    quality = ManifestComparator().compare(base, changed)
    performance = ManifestComparator().compare(base, changed, performance_sensitive=True)

    assert quality.status is Comparability.WARNING
    assert performance.status is Comparability.INCOMPARABLE
    assert any(item.path == "environment.cpu" for item in performance.blocking_differences)


def test_platform_drift_can_be_explicit_candidate_dimension() -> None:
    builder = EvalManifestBuilder()
    baseline = builder.build(
        run=_run(),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )
    candidate = builder.build(
        run=replace(
            _run(run_id="evaluation_run_platform_candidate"),
            snapshot=_snapshot(commit="candidate"),
        ),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )

    blocked = ManifestComparator().compare(baseline, candidate)
    intentional = ManifestComparator().compare(
        baseline,
        candidate,
        candidate_reference_kinds=frozenset({"platform"}),
    )

    assert blocked.status is Comparability.INCOMPARABLE
    assert intentional.status is Comparability.DIRECT
    assert any(
        item.path == "platform.commit" and item.intentional_candidate_dimension
        for item in intentional.differences
    )


def test_sqlite_manifest_survives_restart_and_is_immutable(tmp_path) -> None:
    database = tmp_path / "evaluation.db"
    manifest = EvalManifestBuilder().build(
        run=_run(),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )
    SqliteEvalManifestRepository(database).save_manifest(manifest)

    restored = SqliteEvalManifestRepository(database).get_manifest(manifest.evaluation_run_id)
    assert restored == manifest

    changed = EvalManifestBuilder().build(
        run=replace(_run(), snapshot=_snapshot(commit="different")),
        suite=_suite(),
        seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
    )
    with pytest.raises(ContractError):
        SqliteEvalManifestRepository(database).save_manifest(changed)


def test_provider_private_session_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="provider-private session"):
        ManifestReference("provider_session_id", "secret-session", version="1")

    with pytest.raises(ValueError, match="provider-private session"):
        EvalManifestBuilder().build(
            run=replace(
                _run(),
                snapshot=ConfigurationSnapshot(
                    platform_version="0.0.1",
                    environment=(SnapshotValue("provider_session_id", "abc"),),
                ),
            ),
            suite=_suite(),
            seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
        )


def test_baseline_comparison_requires_compatible_manifests() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        manifests = InMemoryEvalManifestRepository()
        runner = EvaluationRunner(
            repository=repository,
            manifest_repository=manifests,
            executor=StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        baseline = await runner.run_suite(
            suite=_suite(),
            snapshot=_snapshot(),
            seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
        )
        policy = RegressionPolicy(
            policy_id="issue-594.regression",
            version="1",
            rules=(
                RegressionRule(
                    rule_id="pass-to-fail",
                    kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
                ),
            ),
        )
        current = await runner.run_suite(
            suite=_suite(),
            snapshot=_snapshot(),
            baseline_run_id=baseline.run.run_id,
            regression_policy=policy,
            seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
            candidate_reference_kinds=frozenset({"regression_policy"}),
        )

        assert current.manifest_comparison is not None
        assert current.manifest_comparison.status is Comparability.DIRECT
        assert current.comparison is not None
        assert current.comparison.regressions == ()

        with pytest.raises(ValueError, match="incomparable"):
            await runner.run_suite(
                suite=_suite(),
                snapshot=_snapshot(commit="drifted"),
                baseline_run_id=baseline.run.run_id,
                regression_policy=policy,
                seed_policy=SeedPolicy(RandomnessMode.DETERMINISTIC),
                candidate_reference_kinds=frozenset({"regression_policy"}),
            )

    asyncio.run(scenario())
