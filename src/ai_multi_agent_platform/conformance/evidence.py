"""Structured runtime evidence emitted by maintained conformance scenarios.

The conformance runner owns the compatibility report, while canonical subsystem tests
own the resources they exercise. This tiny stdout protocol lets a scenario report the
real canonical IDs and retained evidence references it observed without introducing a
second lifecycle or persistence model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

EVIDENCE_PREFIX = "AI_MULTI_AGENT_PLATFORM_CONFORMANCE_EVIDENCE="


def emit_runtime_evidence(
    *,
    canonical_resource_ids: Sequence[str],
    evidence: Sequence[str],
) -> None:
    """Emit one machine-readable evidence envelope for the parent conformance runner."""

    payload = {
        "canonical_resource_ids": _normalized_strings(
            canonical_resource_ids,
            field="canonical_resource_ids",
        ),
        "evidence": _normalized_strings(evidence, field="evidence"),
    }
    print(EVIDENCE_PREFIX + json.dumps(payload, sort_keys=True))


def parse_runtime_evidence(stdout: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Parse at most one evidence envelope from captured scenario stdout.

    A scenario that does not participate in the runtime evidence protocol returns
    ``None``. Once a marker is present it is treated as an explicit compatibility
    claim and therefore validated fail-closed.
    """

    payload_lines = [
        line[len(EVIDENCE_PREFIX) :]
        for line in stdout.splitlines()
        if line.startswith(EVIDENCE_PREFIX)
    ]
    if not payload_lines:
        return None
    if len(payload_lines) != 1:
        raise ValueError("conformance scenario emitted multiple runtime evidence envelopes")

    try:
        raw = json.loads(payload_lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("conformance scenario emitted malformed runtime evidence JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("conformance runtime evidence must be a JSON object")

    resource_ids = _payload_strings(raw, "canonical_resource_ids")
    evidence = _payload_strings(raw, "evidence")
    if not resource_ids:
        raise ValueError("conformance runtime evidence must include canonical_resource_ids")
    if not evidence:
        raise ValueError("conformance runtime evidence must include evidence references")
    return resource_ids, evidence


def _payload_strings(payload: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"conformance runtime evidence field {field!r} must be a JSON array")
    return tuple(_normalized_strings(value, field=field))


def _normalized_strings(values: Sequence[object], *, field: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"conformance runtime evidence field {field!r} requires non-empty strings"
            )
        item = value.strip()
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return normalized
