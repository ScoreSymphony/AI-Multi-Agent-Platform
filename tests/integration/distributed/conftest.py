from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_TARGET_TEST = "test_shipped_broker_server_cli_and_worker_entrypoints_register_worker"


@pytest.fixture(autouse=True)
def _expose_active_python_scripts_for_operator_entrypoint_test(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt" or request.node.name != _TARGET_TEST:
        return

    scripts_dir = Path(sys.executable).resolve().parent
    current_path = os.environ.get("PATH", "")
    updated_path = f"{scripts_dir}{os.pathsep}{current_path}" if current_path else str(scripts_dir)
    monkeypatch.setenv("PATH", updated_path)
