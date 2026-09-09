from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

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
    RepeatPolicy,
    RepeatStrategy,
    SeedPolicy,
    encode_manifest,
)

_SCHEMA_PATH = Path("schemas/evaluation/eval-manifest-v1.1.schema.json")


def _manifest_payload() -> dict[str, object]:
    suite = EvaluationSuite(
        suite_id="issue-594.schema",
        name="Issue 594 schema",
        version="1",
        cases=(
            EvaluationCase(
                case_id="schema.case",
                name="Schema case",
                version="2",
                fixtures=("fixture://reference@1",),
                timeout_seconds=4.0,
                resource_limits=(SnapshotValue("memory_mb", "128"),),
            ),
        ),
    )
    run = EvaluationRun(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        snapshot=ConfigurationSnapshot(
            platform_version="1.2.3",
            platform_commit="abc123",
            references=(VersionReference("planner", "reference", "2"),),
            environment=(SnapshotValue("python", "3.12"),),
        ),
        repetitions=2,
        run_id="evaluation_run_schema",
    )
    manifest = EvalManifestBuilder().build(
        run=run,
        suite=suite,
        repeat_policy=RepeatPolicy(RepeatStrategy.FIXED_N, 2),
        seed_policy=SeedPolicy(
            RandomnessMode.FIXED_SEED_SUPPORTED,
            ordered_seeds=(7, 11),
            provider_seed_control=True,
        ),
    )
    payload = json.loads(encode_manifest(manifest))
    assert isinstance(payload, dict)
    return payload


def test_eval_manifest_11_payload_validates_against_checked_in_schema() -> None:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    Draft202012Validator(schema).validate(_manifest_payload())


def test_eval_manifest_schema_rejects_run_scoped_digest_shape_drift() -> None:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = _manifest_payload()
    payload["manifest_digest"] = "not-a-sha256"

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)
