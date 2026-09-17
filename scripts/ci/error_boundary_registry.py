"""Machine-verified reviewed classifications for broad exception findings.

The registry is deliberately handler-specific. It is not a path allowlist: a reviewed entry must
match the scanner's structural signature (file, scope, exception form and observed action), every
entry must be consumed exactly once, and every otherwise-unresolved production finding must have a
matching review. Registry entries can never authorize swallowing cancellation or process-control
signals.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

_CLASSIFICATION_MAP = {
    "BOUNDARY": "boundary catch",
    "CLEANUP / BEST EFFORT": "cleanup / best effort",
    "DOMAIN / CONTRACT TRANSLATION": "domain error translation",
    "RETRY BOUNDARY": "boundary catch",
    "PROCESS / SHUTDOWN BOUNDARY": "cleanup / best effort",
}
_REQUIRED_ENTRY_FIELDS = frozenset(
    {
        "file",
        "scope",
        "exception_form",
        "current_action",
        "classification",
        "decision",
        "rationale",
    }
)


def _signature(item: Any) -> tuple[str, str, str, str]:
    return (
        str(item.file),
        str(item.scope),
        str(item.exception_form),
        str(item.current_action),
    )


def _entry_signature(entry: dict[str, object]) -> tuple[str, str, str, str]:
    return (
        str(entry["file"]),
        str(entry["scope"]),
        str(entry["exception_form"]),
        str(entry["current_action"]),
    )


def _load_entries(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "cannot load error-boundary classification registry: " f"{type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("error-boundary classification registry must use schema_version=1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("error-boundary classification registry entries must be a list")

    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"classification entry {index} must be an object")
        missing = _REQUIRED_ENTRY_FIELDS.difference(raw)
        if missing:
            raise ValueError(
                f"classification entry {index} is missing fields: {', '.join(sorted(missing))}"
            )
        classification = raw.get("classification")
        if classification not in _CLASSIFICATION_MAP:
            raise ValueError(
                f"classification entry {index} has unsupported classification {classification!r}"
            )
        if not str(raw.get("decision", "")).strip():
            raise ValueError(f"classification entry {index} must include a decision")
        if not str(raw.get("rationale", "")).strip():
            raise ValueError(f"classification entry {index} must include a rationale")
        normalized.append(dict(raw))
    return normalized


def apply_review_registry[T](findings: Iterable[T], registry_path: Path | None) -> list[T]:
    """Apply an exact reviewed registry and fail closed on drift.

    When ``registry_path`` is ``None`` the scanner keeps its structural recommendations unchanged.
    When a registry is supplied it becomes an acceptance inventory: all otherwise unresolved
    production findings must match exactly, no registry entry may be stale, and process-control risk
    can never be downgraded by review metadata.
    """

    materialized = list(findings)
    if registry_path is None:
        return materialized

    entries = _load_entries(registry_path)
    by_signature: dict[tuple[str, str, str, str], deque[dict[str, object]]] = defaultdict(deque)
    for entry in entries:
        by_signature[_entry_signature(entry)].append(entry)

    reviewed: list[T] = []
    unresolved: list[Any] = []
    for item in materialized:
        if (
            getattr(item, "source_class", None) != "production"
            or getattr(item, "severity", None) == "allowed"
        ):
            reviewed.append(item)
            continue

        bucket = by_signature.get(_signature(item))
        if not bucket:
            unresolved.append(item)
            reviewed.append(item)
            continue

        entry = bucket.popleft()
        if getattr(item, "cancellation_risk", False) or getattr(item, "shutdown_risk", False):
            raise ValueError(
                "review registry cannot authorize cancellation/process-control risk at "
                f"{item.file}:{item.line} [{item.scope}]"
            )

        registry_classification = str(entry["classification"])
        classification = _CLASSIFICATION_MAP[registry_classification]
        decision = str(entry["decision"]).strip()
        rationale = str(entry["rationale"]).strip()
        reviewed.append(
            replace(
                item,
                recommended_classification=classification,
                recommended_action=f"{decision}: {rationale}",
                justification=f"registry:{registry_classification}",
                severity="allowed",
            )
        )

    stale: list[dict[str, object]] = [entry for bucket in by_signature.values() for entry in bucket]
    if unresolved or stale:
        messages: list[str] = []
        if unresolved:
            sample = "; ".join(
                f"{item.file}:{item.line} [{item.scope}] "
                f"{item.exception_form}/{item.current_action}"
                for item in unresolved[:8]
            )
            messages.append(f"unclassified production findings={len(unresolved)} ({sample})")
        if stale:
            sample = "; ".join(
                f"{entry['file']} [{entry['scope']}] "
                f"{entry['exception_form']}/{entry['current_action']}"
                for entry in stale[:8]
            )
            messages.append(f"stale classification entries={len(stale)} ({sample})")
        raise ValueError(
            "error-boundary classification registry is out of sync: " + " | ".join(messages)
        )

    return reviewed
