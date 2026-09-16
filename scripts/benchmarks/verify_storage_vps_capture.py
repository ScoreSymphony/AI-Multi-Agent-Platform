#!/usr/bin/env python3
"""Verify an ordinary-VPS object-storage evidence capture."""

from __future__ import annotations

import runpy
from pathlib import Path

HARNESS = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "evidence"
    / "issue_862"
    / "verify_storage_vps_capture_harness.py"
)

if __name__ == "__main__":
    runpy.run_path(str(HARNESS), run_name="__main__")
