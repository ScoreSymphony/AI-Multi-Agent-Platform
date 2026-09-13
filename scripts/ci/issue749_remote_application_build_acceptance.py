"""Focused acceptance gate for issue #749 remote application builds."""

from __future__ import annotations

import subprocess
import sys

PYTEST_TARGETS = (
    "tests/integration/application_distribution/test_application_build_requirements.py",
    "tests/integration/application_distribution/test_remote_build_worker_lifecycle.py",
    "tests/contract/application_distribution/test_placement_conformance.py",
    "tests/integration/application_distribution/test_remote_worker_builds.py",
    "tests/integration/application_distribution/test_remote_worker_multitarget.py",
    "tests/integration/application_distribution/test_remote_worker_recovery.py",
    "tests/integration/application_distribution/test_remote_worker_result_evidence.py",
    "tests/integration/application_distribution/test_remote_worker_restart.py",
    "tests/integration/application_distribution/test_remote_worker_lost_replies.py",
    "tests/integration/application_distribution/test_remote_build_security.py",
    (
        "tests/integration/application_distribution/"
        "test_execution_and_github_connector.py::"
        "test_application_distribution_executes_build_and_admits_canonical_artifact"
    ),
)


def main() -> int:
    """Run the complete maintained #749 acceptance matrix as one explicit gate."""

    command = [sys.executable, "-m", "pytest", "-q", *PYTEST_TARGETS]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
