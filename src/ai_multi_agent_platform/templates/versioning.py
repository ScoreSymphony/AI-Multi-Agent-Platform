"""Small dependency-free version-constraint matcher for canonical Template compatibility."""

from __future__ import annotations

import re

_NUMERIC_VERSION = re.compile(r"^\d+(?:\.\d+){0,2}$")
_COMPARATOR = re.compile(r"^(>=|<=|==|!=|>|<)?\s*(\d+(?:\.\d+){0,2})$")


def version_satisfies(version: str, constraint: str) -> bool:
    """Return whether ``version`` satisfies a conservative numeric constraint.

    Supported range syntax is a comma-separated conjunction of ``>=``, ``<=``, ``>``,
    ``<``, ``==`` and ``!=`` comparisons, for example ``>=1,<2``. A bare value is an
    exact match. Ordering is defined only for one-to-three-part dotted numeric versions;
    arbitrary provider labels therefore fail closed for ordered comparisons.
    """

    actual = version.strip()
    requested = constraint.strip()
    if not actual or not requested:
        return False

    parts = tuple(part.strip() for part in requested.split(","))
    if any(not part for part in parts):
        return False

    for part in parts:
        match = _COMPARATOR.fullmatch(part)
        if match is None:
            # Bare non-numeric labels are still useful as exact contract/provider versions.
            if len(parts) == 1 and not _looks_comparator_prefixed(part):
                return actual == part
            return False

        operator = match.group(1)
        expected_text = match.group(2)
        if operator is None:
            return len(parts) == 1 and _numeric_equal(actual, expected_text)
        if not _NUMERIC_VERSION.fullmatch(actual):
            return False

        actual_key = _version_key(actual)
        expected_key = _version_key(expected_text)
        if operator == ">=" and not actual_key >= expected_key:
            return False
        if operator == "<=" and not actual_key <= expected_key:
            return False
        if operator == ">" and not actual_key > expected_key:
            return False
        if operator == "<" and not actual_key < expected_key:
            return False
        if operator == "==" and not actual_key == expected_key:
            return False
        if operator == "!=" and not actual_key != expected_key:
            return False

    return True


def any_version_satisfies(versions: tuple[str, ...], constraint: str) -> bool:
    """Return true when at least one canonical available version satisfies a requirement."""

    return any(version_satisfies(version, constraint) for version in versions)


def _numeric_equal(actual: str, expected: str) -> bool:
    if not _NUMERIC_VERSION.fullmatch(actual):
        return False
    return _version_key(actual) == _version_key(expected)


def _version_key(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(".")]
    parts.extend([0] * (3 - len(parts)))
    return (parts[0], parts[1], parts[2])


def _looks_comparator_prefixed(value: str) -> bool:
    return value.startswith((">", "<", "=", "!"))
