from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "ci" / "package_dependency_audit.py"
BASELINE = ROOT / "docs" / "PACKAGE_DEPENDENCY_CYCLES.toml"


def test_dependency_cycle_baseline_enforcement_stays_enabled() -> None:
    with BASELINE.open("rb") as handle:
        baseline = tomllib.load(handle)

    assert baseline.get("enforce") is True


def test_top_level_package_dependency_cycles_do_not_expand() -> None:
    completed = subprocess.run(
        [sys.executable, str(AUDIT), "--baseline", str(BASELINE)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
