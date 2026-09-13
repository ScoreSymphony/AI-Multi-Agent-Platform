from __future__ import annotations

from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationRun,
    EvaluationSuite,
    SnapshotValue,
    VersionReference,
)
from ai_multi_agent_platform.evaluation.reproducibility import (
    EvalManifestBuilder,
    RandomnessMode,
    SeedPolicy,
)


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="issue-594.identity",
        name="Issue 594 manifest identity",
        version="1",
        cases=(
            EvaluationCase(
                case_id="identity.case",
                name="Identity case",
                version="1",
            ),
        ),
    )


def _snapshot() -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        platform_version="1.0.0",
        platform_commit="abc123",
        references=(VersionReference("model", "local", "1"),),
        environment=(SnapshotValue("python", "3.12"),),
    )


def _run(run_id: str) -> EvaluationRun:
    return EvaluationRun(
        suite_id="issue-594.identity",
        suite_version="1",
        snapshot=_snapshot(),
        run_id=run_id,
    )


def test_manifest_digest_is_run_independent_but_manifest_id_is_run_scoped() -> None:
    builder = EvalManifestBuilder()
    seed_policy = SeedPolicy(RandomnessMode.DETERMINISTIC)

    first = builder.build(run=_run("run-a"), suite=_suite(), seed_policy=seed_policy)
    second = builder.build(run=_run("run-b"), suite=_suite(), seed_policy=seed_policy)

    assert first.manifest_digest == second.manifest_digest
    assert first.manifest_id != second.manifest_id
    assert first.reproducibility_payload() == second.reproducibility_payload()
    assert first.canonical_payload() != second.canonical_payload()
