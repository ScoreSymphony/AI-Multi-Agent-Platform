from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ai_multi_agent_platform.conformance.optional_profiles import _OPTIONAL_EVIDENCE


def _registered_pytest_nodes() -> tuple[str, ...]:
    prefix = (sys.executable, "-m", "pytest", "-q")
    nodes: list[str] = []
    for command in _OPTIONAL_EVIDENCE.values():
        if command[:4] == prefix:
            nodes.extend(command[4:])
    return tuple(nodes)


def test_registered_optional_pytest_nodes_collect_successfully() -> None:
    nodes = _registered_pytest_nodes()

    assert nodes, "expected optional conformance Pytest evidence to be registered"

    repository_root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            *nodes,
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, (
        "registered optional conformance Pytest selectors must remain collectable\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
