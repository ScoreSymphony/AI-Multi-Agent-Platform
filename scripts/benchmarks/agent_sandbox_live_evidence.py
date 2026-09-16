#!/usr/bin/env python3
"""Run the pinned Agent-Sandbox live-evidence harness."""

from __future__ import annotations

import runpy
from pathlib import Path

HARNESS = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "evidence"
    / "issue_798"
    / "agent_sandbox_live_harness.py"
)

if __name__ == "__main__":
    runpy.run_path(str(HARNESS), run_name="__main__")
