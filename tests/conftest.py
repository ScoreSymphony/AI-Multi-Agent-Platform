"""Repository-wide test configuration.

Most historical Control Plane contract tests intentionally exercise transport/domain
semantics without installing an authorization policy. Production composition is now
secure-by-default, so tests opt into that legacy unsecured mode explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("AI_MULTI_AGENT_PLATFORM_ALLOW_INSECURE_CONTROL_PLANE", "1")

_TEST_ROOT = Path(__file__).resolve().parent
_SUITE_MARKERS = frozenset(
    {
        "unit",
        "contract",
        "integration",
        "e2e",
        "performance",
        "regression",
        "release",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply the canonical suite marker from a test's first directory below ``tests/``."""
    for item in items:
        try:
            relative_path = Path(str(item.path)).resolve().relative_to(_TEST_ROOT)
        except ValueError:
            continue
        if not relative_path.parts:
            continue
        suite = relative_path.parts[0]
        if suite in _SUITE_MARKERS:
            item.add_marker(getattr(pytest.mark, suite))
