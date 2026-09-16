#!/usr/bin/env python3
"""Run the pinned SWE-ReX live-runtime evidence harness."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

HARNESS = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "evidence"
    / "issue_861"
    / "swe_rex_live_harness.py"
)


def _bridge_legacy_evidence_environment() -> None:
    """Translate stable entry-point settings to the historical evidence contract."""

    mappings = {
        "SWEREX_EVIDENCE_IMAGE": "ISSUE861_SWEREX_IMAGE",
        "SWEREX_EVIDENCE_DOCKER_NETWORK": "ISSUE861_DOCKER_NETWORK",
    }
    for stable_name, evidence_name in mappings.items():
        value = os.environ.get(stable_name)
        if value and evidence_name not in os.environ:
            os.environ[evidence_name] = value


if __name__ == "__main__":
    _bridge_legacy_evidence_environment()
    runpy.run_path(str(HARNESS), run_name="__main__")
