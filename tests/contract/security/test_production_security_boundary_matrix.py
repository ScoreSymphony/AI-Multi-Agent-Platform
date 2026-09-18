from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "scripts/ci/security_boundary_conformance.py"


def test_production_security_boundary_matrix_is_complete_and_resolvable() -> None:
    result = subprocess.run(
        (sys.executable, str(RUNNER), "--validate-only"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "17 surfaces" in result.stdout
    assert "security boundary matrix: valid" in result.stdout
