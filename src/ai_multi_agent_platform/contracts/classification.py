"""Canonical data-classification semantics shared by every egress boundary."""

from __future__ import annotations

from enum import StrEnum


class DataClassification(StrEnum):
    """Portable platform-owned data sensitivity classification.

    ``PRIVATE``, ``RESTRICTED`` and ``SECRET_REFERENCE`` preserve persisted vocabulary
    introduced before issue #591. New code should prefer the five canonical categories
    ``PUBLIC``, ``INTERNAL``, ``CONFIDENTIAL``, ``SECRET`` and ``REGULATED``.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PRIVATE = "private"
    RESTRICTED = "restricted"
    SECRET = "secret"
    SECRET_REFERENCE = "secret_reference"
    REGULATED = "regulated"


class ClassificationDowngradeError(ValueError):
    """Raised when derived data attempts to silently lower its classification."""


_STRENGTH: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.PRIVATE: 2,
    DataClassification.RESTRICTED: 3,
    DataClassification.SECRET: 4,
    DataClassification.SECRET_REFERENCE: 4,
    DataClassification.REGULATED: 5,
}

# Deterministic tie-breaking prefers canonical categories over compatibility vocabulary.
_PREFERENCE: dict[DataClassification, int] = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 0,
    DataClassification.PRIVATE: 0,
    DataClassification.CONFIDENTIAL: 1,
    DataClassification.RESTRICTED: 0,
    DataClassification.SECRET_REFERENCE: 0,
    DataClassification.SECRET: 1,
    DataClassification.REGULATED: 0,
}


def classification_strength(value: DataClassification) -> int:
    """Return the monotonic sensitivity rank for one classification."""

    return _STRENGTH[value]


def strongest_classification(
    *values: DataClassification | None,
) -> DataClassification | None:
    """Merge classifications without ever weakening the strongest source."""

    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    return max(present, key=lambda item: (_STRENGTH[item], _PREFERENCE[item]))


def require_monotonic_classification(
    source: DataClassification,
    derived: DataClassification,
    *,
    redacted: bool = False,
    policy_decision_id: str | None = None,
) -> DataClassification:
    """Validate a derived classification against its source.

    A lower classification is only valid when an explicit policy decision records that
    redaction/minimization produced a derivative payload. Merely relabelling content is
    never sufficient to downgrade it.
    """

    if classification_strength(derived) >= classification_strength(source):
        return derived
    if redacted and policy_decision_id is not None and policy_decision_id.strip():
        return derived
    raise ClassificationDowngradeError(
        f"classification downgrade {source.value!r} -> {derived.value!r} requires "
        "an explicit redaction policy decision"
    )
