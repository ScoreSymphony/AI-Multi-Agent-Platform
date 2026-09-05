"""Build provenance discovery for deterministic backup/restore compatibility checks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BUILD_COMMIT_ENV = "AI_MULTI_AGENT_PLATFORM_BUILD_COMMIT"


class BuildProvenanceError(RuntimeError):
    """Raised when an operator backup/restore cannot identify the running build."""


def discover_build_commit(explicit: str | None = None) -> str | None:
    """Resolve the current build commit from explicit input, environment, or a source checkout."""

    if explicit is not None:
        value = explicit.strip()
        if not value:
            raise BuildProvenanceError("platform build commit must not be blank")
        return value

    configured = os.environ.get(BUILD_COMMIT_ENV)
    if configured is not None:
        value = configured.strip()
        if not value:
            raise BuildProvenanceError(f"{BUILD_COMMIT_ENV} must not be blank when configured")
        return value

    checkout = _source_checkout_root()
    if checkout is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def require_build_commit(explicit: str | None = None) -> str:
    """Resolve an exact build identity or fail with an operator-actionable diagnostic."""

    commit = discover_build_commit(explicit)
    if commit is None:
        raise BuildProvenanceError(
            "exact platform build commit is unavailable; run from a Git checkout, pass "
            "--platform-commit/--expected-platform-commit, or set "
            f"{BUILD_COMMIT_ENV}"
        )
    return commit


def _source_checkout_root() -> Path | None:
    location = Path(__file__).resolve()
    for parent in location.parents:
        if (parent / ".git").exists():
            return parent
    return None


__all__ = [
    "BUILD_COMMIT_ENV",
    "BuildProvenanceError",
    "discover_build_commit",
    "require_build_commit",
]
