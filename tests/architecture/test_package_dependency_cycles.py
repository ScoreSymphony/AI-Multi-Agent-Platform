from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "ci" / "package_dependency_audit.py"
BASELINE = ROOT / "docs" / "PACKAGE_DEPENDENCY_CYCLES.toml"


def test_top_level_package_dependency_cycles_do_not_expand() -> None:
    completed = subprocess.run(
        [sys.executable, str(AUDIT), "--baseline", str(BASELINE)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
