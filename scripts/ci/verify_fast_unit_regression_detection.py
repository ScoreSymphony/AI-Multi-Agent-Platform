"""Prove that a deterministic domain regression is caught by the fast unit layer.

The probe mutates a temporary copy of the production source tree only. It deliberately changes the
preferred-worker placement weight and then runs the focused unit invariant that owns that policy.
The repository working tree is never modified and no database, Worker, HTTP server or provider is
started.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
POLICY_PATH = Path("ai_multi_agent_platform/distributed/placement_policy.py")
TARGET_TEST = (
    "tests/unit/distributed/test_placement_policy.py"
    "::test_preference_score_has_explicit_additive_precedence"
)
ORIGINAL_INVARIANT = "score += 1000"
MUTATED_INVARIANT = "score += 999"
EXPECTED_FAILURE_FRAGMENT = "test_preference_score_has_explicit_additive_precedence"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fast-unit-regression-") as temporary_directory:
        temporary_source = Path(temporary_directory) / "src"
        shutil.copytree(SOURCE_ROOT, temporary_source)

        policy_file = temporary_source / POLICY_PATH
        source = policy_file.read_text(encoding="utf-8")
        if source.count(ORIGINAL_INVARIANT) != 1:
            raise RuntimeError(
                "controlled regression probe expected exactly one preferred-worker score invariant"
            )
        policy_file.write_text(
            source.replace(ORIGINAL_INVARIANT, MUTATED_INVARIANT, 1),
            encoding="utf-8",
        )

        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(temporary_source)
        if existing_pythonpath:
            environment["PYTHONPATH"] += os.pathsep + existing_pythonpath

        started = perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "pytest", TARGET_TEST, "-q"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed_seconds = perf_counter() - started

    output = result.stdout + result.stderr
    if result.returncode == 0:
        print(output)
        raise RuntimeError("fast unit layer did not detect the controlled placement regression")
    if EXPECTED_FAILURE_FRAGMENT not in output or "1 failed" not in output:
        print(output)
        raise RuntimeError(
            "regression probe failed for an unexpected reason instead of the targeted "
            "unit invariant"
        )

    print(
        "Controlled placement-policy regression detected by the fast unit layer "
        f"in {elapsed_seconds:.3f}s (expected test failure observed)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
