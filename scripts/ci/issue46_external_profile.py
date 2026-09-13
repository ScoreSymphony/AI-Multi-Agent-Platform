"""Fail-closed runner for external optional #46 adapter profiles."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ai_multi_agent_platform.adapters.hermes import HERMES_PINNED_REVISION

_HERMES_CANDIDATE_ENV = "HERMES_COMPATIBILITY_CANDIDATE_REVISION"


def _run_pytest(*nodes: str) -> int:
    return subprocess.run(
        (sys.executable, "-m", "pytest", "-q", *nodes),
        check=False,
    ).returncode


def _checkout_revision(upstream: Path) -> str | None:
    try:
        completed = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=upstream,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _hermes() -> int:
    upstream_value = os.getenv("HERMES_UPSTREAM_DIR")
    revision = os.getenv("HERMES_UPSTREAM_REVISION")
    candidate_revision = os.getenv(_HERMES_CANDIDATE_ENV)
    if not upstream_value:
        print("Hermes profile requires HERMES_UPSTREAM_DIR", file=sys.stderr)
        return 2
    upstream = Path(upstream_value).resolve()
    if not (upstream / "gateway" / "platforms" / "api_server.py").is_file():
        print(f"HERMES_UPSTREAM_DIR is not a Hermes source checkout: {upstream}", file=sys.stderr)
        return 2

    if candidate_revision:
        if revision != candidate_revision:
            print(
                "Hermes candidate profile requires HERMES_UPSTREAM_REVISION to match "
                f"{_HERMES_CANDIDATE_ENV} exactly; expected {candidate_revision}, got {revision!r}",
                file=sys.stderr,
            )
            return 2
        actual_revision = _checkout_revision(upstream)
        if actual_revision != candidate_revision:
            print(
                "Hermes candidate profile requires the source checkout HEAD to match the "
                "declared candidate exactly; "
                f"expected {candidate_revision}, got {actual_revision!r}",
                file=sys.stderr,
            )
            return 2
        return _run_pytest(
            "tests/integration/models/test_hermes_adapter.py::"
            "test_hermes_agent_mapper_pins_agent_team_model_and_capability_contracts",
            "tests/regression/upstreams/test_hermes_v0_21_2.py",
            "tests/integration/upstreams/test_hermes_v0_21_2_candidate.py",
            "tests/integration/upstreams/test_hermes_v0_21_2_concurrency.py",
            "tests/integration/upstreams/test_hermes_v0_21_2_restart.py",
        )

    if revision != HERMES_PINNED_REVISION:
        print(
            "Hermes profile requires the exact pinned revision "
            f"{HERMES_PINNED_REVISION}; got {revision!r}",
            file=sys.stderr,
        )
        return 2
    return _run_pytest(
        "tests/integration/models/test_hermes_adapter.py::"
        "test_hermes_agent_mapper_pins_agent_team_model_and_capability_contracts",
        "tests/integration/upstreams/test_hermes_pinned.py::"
        "test_pinned_hermes_declares_required_run_lifecycle_surface",
        "tests/integration/upstreams/test_hermes_pinned.py::"
        "test_adapter_against_pinned_hermes_runs_api",
        "tests/integration/upstreams/test_hermes_pinned.py::"
        "test_kernel_uses_real_pinned_hermes_and_reference_executor",
        "tests/regression/upstreams/test_hermes_v0_21_2.py",
        "tests/integration/upstreams/test_hermes_v0_21_2_candidate.py",
        "tests/integration/upstreams/test_hermes_v0_21_2_concurrency.py",
        "tests/integration/upstreams/test_hermes_v0_21_2_restart.py",
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["B"]:
        print(
            "usage: python scripts/ci/issue46_external_profile.py B",
            file=sys.stderr,
        )
        return 2
    return _hermes()


if __name__ == "__main__":
    raise SystemExit(main())
