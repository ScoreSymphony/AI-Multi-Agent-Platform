#!/usr/bin/env python3
"""Run the pinned SWE-ReX canonical Executor evidence harness."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

HARNESS = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "evidence"
    / "issue_861"
    / "swe_rex_canonical_harness.py"
)


def _bridge_legacy_evidence_environment() -> None:
    """Translate stable entry-point settings to the historical evidence contract."""

    value = os.environ.get("SWEREX_EVIDENCE_IMAGE")
    if value and "ISSUE861_SWEREX_IMAGE" not in os.environ:
        os.environ["ISSUE861_SWEREX_IMAGE"] = value


if __name__ == "__main__":
    _bridge_legacy_evidence_environment()
    runpy.run_path(str(HARNESS), run_name="__main__")
