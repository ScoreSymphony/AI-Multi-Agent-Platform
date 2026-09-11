"""Validated local release-gate policy loading for the shipped single-node profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from ai_multi_agent_platform.application_distribution import (
    DeterministicGateCheck,
    ReleaseGateKind,
    ReleaseGateRequirement,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.configuration import ConfigurationError

_RELEASE_GATE_POLICY_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "requirements"],
    "properties": {
        "schema_version": {"const": 1},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "kind"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "kind": {
                        "enum": [
                            ReleaseGateKind.DETERMINISTIC.value,
                            ReleaseGateKind.VERIFICATION.value,
                            ReleaseGateKind.EVALUATION.value,
                        ]
                    },
                    "target_id": {"type": ["string", "null"], "minLength": 1},
                    "deterministic_check": {
                        "type": ["string", "null"],
                        "enum": [
                            None,
                            DeterministicGateCheck.ARTIFACT_EXISTS.value,
                            DeterministicGateCheck.FILE_CHECKSUM.value,
                            DeterministicGateCheck.MANIFEST_CHECKSUM.value,
                        ],
                    },
                    "verification_policy_id": {"type": ["string", "null"], "minLength": 1},
                    "verification_policy_version": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                    },
                    "verification_stage_id": {"type": ["string", "null"], "minLength": 1},
                    "evaluation_suite_id": {"type": ["string", "null"], "minLength": 1},
                    "evaluation_suite_version": {"type": ["string", "null"], "minLength": 1},
                },
            },
        },
    },
}
_POLICY_VALIDATOR = Draft202012Validator(_RELEASE_GATE_POLICY_SCHEMA)


def load_application_release_gate_policy(path: Path) -> StaticReleaseGatePolicy:
    """Load one explicit, secret-free gate-name→canonical-mechanism catalog."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read application release gate policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError("application release gate policy must be valid JSON") from exc

    errors = sorted(_POLICY_VALIDATOR.iter_errors(raw), key=lambda item: list(item.absolute_path))
    if errors:
        locations = []
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            locations.append(f"{location}: {error.message}")
        raise ConfigurationError(
            "application release gate policy does not match schema: " + "; ".join(locations)
        )

    document = cast(dict[str, object], raw)
    raw_requirements = cast(list[dict[str, object]], document["requirements"])
    requirements: list[ReleaseGateRequirement] = []
    try:
        for item in raw_requirements:
            deterministic = item.get("deterministic_check")
            requirements.append(
                ReleaseGateRequirement(
                    name=cast(str, item["name"]),
                    kind=ReleaseGateKind(cast(str, item["kind"])),
                    target_id=cast(str | None, item.get("target_id")),
                    deterministic_check=(
                        None
                        if deterministic is None
                        else DeterministicGateCheck(cast(str, deterministic))
                    ),
                    verification_policy_id=cast(
                        str | None, item.get("verification_policy_id")
                    ),
                    verification_policy_version=cast(
                        int | None, item.get("verification_policy_version")
                    ),
                    verification_stage_id=cast(
                        str | None, item.get("verification_stage_id")
                    ),
                    evaluation_suite_id=cast(str | None, item.get("evaluation_suite_id")),
                    evaluation_suite_version=cast(
                        str | None, item.get("evaluation_suite_version")
                    ),
                )
            )
        return StaticReleaseGatePolicy(tuple(requirements))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"invalid application release gate requirement: {exc}") from exc
