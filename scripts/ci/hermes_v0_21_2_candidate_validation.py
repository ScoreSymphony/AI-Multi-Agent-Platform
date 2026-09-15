"""Run revision-bound Hermes Agent v0.21.2 candidate validation.

This runner intentionally does not mutate or depend on the accepted Hermes pin. It
validates the candidate first; promotion of HERMES_PINNED_REVISION happens only after
this gate and the repository-wide gates pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERMES_V0_21_2_TAG = "v2026.9.11"
HERMES_V0_21_2_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def _run(*args: str, cwd: Path | None = None) -> int:
    return subprocess.run(
        (sys.executable, "-m", "pytest", "-q", *args),
        cwd=cwd,
        check=False,
    ).returncode


def _candidate_checkout() -> Path:
    upstream_value = os.getenv("HERMES_UPSTREAM_DIR")
    declared_revision = os.getenv("HERMES_UPSTREAM_REVISION")
    if not upstream_value:
        raise RuntimeError("HERMES_UPSTREAM_DIR is required for the Hermes candidate gate")
    if declared_revision != HERMES_V0_21_2_REVISION:
        raise RuntimeError(
            "Hermes candidate gate requires exact revision "
            f"{HERMES_V0_21_2_REVISION}; got {declared_revision!r}"
        )

    upstream = Path(upstream_value).resolve()
    if not (upstream / "gateway" / "platforms" / "api_server.py").is_file():
        raise RuntimeError(f"HERMES_UPSTREAM_DIR is not a Hermes source checkout: {upstream}")

    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=upstream,
        check=True,
        capture_output=True,
        text=True,
    )
    actual_revision = completed.stdout.strip()
    if actual_revision != HERMES_V0_21_2_REVISION:
        raise RuntimeError(
            "Hermes candidate checkout drifted: "
            f"expected {HERMES_V0_21_2_REVISION}, got {actual_revision}"
        )
    return upstream


def main() -> int:
    try:
        upstream = _candidate_checkout()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Extend the accepted v0.21.1 adapter matrix with candidate-bound regressions,
    # then exercise the exact candidate's real /v1/runs seam, cancellation isolation,
    # and durable active-run recovery after runtime recreation.
    platform_status = _run(
        "tests/regression/upstreams/test_hermes_v0_21_2.py",
        "tests/integration/upstreams/test_hermes_v0_21_2_candidate.py",
        "tests/integration/upstreams/test_hermes_v0_21_2_concurrency.py",
        "tests/integration/upstreams/test_hermes_v0_21_2_restart.py",
    )
    if platform_status:
        return platform_status

    # Upstream's own release-specific state.db regressions. These are run from the
    # exact source checkout so the evidence is tied to the tagged implementation,
    # not merely to release-note claims. The write-lock patience suite supplies the
    # explicit legitimate writer-vs-writer contention case required by this gate.
    upstream_status = _run(
        "tests/hermes_state/test_corrupt_row_robustness.py",
        "tests/hermes_state/test_read_path_transient_ioerr.py",
        "tests/hermes_state/test_wal_active_confirmed.py",
        "tests/hermes_state/test_deleted_wal_generation_guard.py",
        "tests/hermes_state/test_shared_session_db_registry.py",
        "tests/state/test_write_lock_patience.py",
        "tests/tui_gateway/test_session_resume_db_ownership.py",
        "tests/tui_gateway/test_launch_db_home_override_race.py",
        cwd=upstream,
    )
    if upstream_status:
        return upstream_status

    profile_reader_status = _run(
        "tests/tui_gateway/test_profiles_list_canonical_session.py::"
        "test_profiles_list_does_not_wait_out_write_lock",
        cwd=upstream,
    )
    if profile_reader_status:
        return profile_reader_status

    reader_writer_status = _run(
        "tests/test_hermes_state.py",
        "-k",
        (
            "test_settled_open_issues_no_main_db_writes or "
            "test_open_completes_while_sibling_holds_write_lock"
        ),
        cwd=upstream,
    )
    if reader_writer_status:
        return reader_writer_status

    restart_status = _run(
        "tests/gateway/test_api_server_runs.py",
        "-k",
        "dead_owner_nonterminal_status_becomes_interrupted",
        cwd=upstream,
    )
    return restart_status


if __name__ == "__main__":
    raise SystemExit(main())
