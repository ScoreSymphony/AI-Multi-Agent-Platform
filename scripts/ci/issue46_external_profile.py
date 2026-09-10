"""Fail-closed runner for external optional #46 adapter profiles."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ai_multi_agent_platform.adapters.hermes import HERMES_PINNED_REVISION


def _run_pytest(*nodes: str) -> int:
    return subprocess.run(
        (sys.executable, "-m", "pytest", "-q", *nodes),
        check=False,
    ).returncode


def _hermes() -> int:
    upstream_value = os.getenv("HERMES_UPSTREAM_DIR")
    revision = os.getenv("HERMES_UPSTREAM_REVISION")
    if not upstream_value:
        print("Hermes profile requires HERMES_UPSTREAM_DIR", file=sys.stderr)
        return 2
    upstream = Path(upstream_value).resolve()
    if not (upstream / "gateway" / "platforms" / "api_server.py").is_file():
        print(f"HERMES_UPSTREAM_DIR is not a Hermes source checkout: {upstream}", file=sys.stderr)
        return 2
    if revision != HERMES_PINNED_REVISION:
        print(
            "Hermes profile requires the exact pinned revision "
            f"{HERMES_PINNED_REVISION}; got {revision!r}",
            file=sys.stderr,
        )
        return 2
    return _run_pytest(
        "tests/test_issue_8_hermes_adapter.py::"
        "test_hermes_agent_mapper_pins_agent_team_model_and_capability_contracts",
        "tests/integration/upstreams/test_hermes_pinned.py::"
        "test_adapter_against_pinned_hermes_runs_api",
        "tests/integration/upstreams/test_hermes_pinned.py::"
        "test_kernel_uses_real_pinned_hermes_and_reference_executor",
    )


def _forge() -> int:
    base_url = os.getenv("FORGE_SIDECAR_BASE_URL")
    workspace_root = os.getenv("FORGE_SIDECAR_WORKSPACE_ROOT")
    if not base_url or not workspace_root:
        print(
            "Forge profile requires FORGE_SIDECAR_BASE_URL and FORGE_SIDECAR_WORKSPACE_ROOT",
            file=sys.stderr,
        )
        return 2
    if not Path(workspace_root).is_dir():
        print(f"Forge workspace root does not exist: {workspace_root}", file=sys.stderr)
        return 2
    return _run_pytest(
        "tests/integration/forge/test_sidecar.py::"
        "test_real_sidecar_health_and_execution_preserve_canonical_identity",
        "tests/integration/forge/test_sidecar.py::"
        "test_real_sidecar_executes_through_canonical_kernel_lifecycle",
        "tests/integration/forge/test_sidecar.py::test_real_sidecar_cancellation_stays_canonical",
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"B", "C"}:
        print(
            "usage: python scripts/ci/issue46_external_profile.py B|C",
            file=sys.stderr,
        )
        return 2
    return _hermes() if args[0] == "B" else _forge()


if __name__ == "__main__":
    raise SystemExit(main())
