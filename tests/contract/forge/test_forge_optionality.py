from __future__ import annotations

import subprocess
import sys


def test_execution_core_does_not_import_forge_adapter() -> None:
    script = """
import sys
import ai_multi_agent_platform.execution
assert 'ai_multi_agent_platform.adapters.forge' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)
