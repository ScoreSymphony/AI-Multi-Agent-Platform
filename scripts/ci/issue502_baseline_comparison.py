#!/usr/bin/env python3
"""Measure the deterministic Git reference baseline for issue #502.

This module intentionally stays stdlib-only so the ProjectAtlas pilot can compare one real,
local baseline path against the pinned candidate without adding a paid or hosted dependency.
The comparison is evidence for an experimental/deferred decision, not proof that either path
should become the default provider.
"""

from __future__ import annotations

import resource
import subprocess
import time
from pathlib import Path
from typing import Any

_COMMAND_TIMEOUT_SECONDS = 30


def _run_git(
    source_root: Path,
    environment: dict[str, str],
    args: list[str],
) -> tuple[str, dict[str, float | int | str]]:
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter_ns()
    completed = subprocess.run(
        ["git", *args],
        cwd=source_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
        close_fds=True,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if completed.returncode != 0:
        raise RuntimeError(
            f"baseline git command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout={completed.stdout[:1000]!r}\nstderr={completed.stderr[:1000]!r}"
        )
    return completed.stdout, {
        "command": "git " + " ".join(args),
        "elapsed_ms": elapsed_ms,
        "stdout_bytes": len(completed.stdout.encode("utf-8")),
        "stderr_bytes": len(completed.stderr.encode("utf-8")),
        "user_cpu_seconds": max(0.0, usage_after.ru_utime - usage_before.ru_utime),
        "system_cpu_seconds": max(0.0, usage_after.ru_stime - usage_before.ru_stime),
    }


def measure_git_reference_baseline(
    *,
    source_root: Path,
    revision: str,
    environment: dict[str, str],
) -> dict[str, Any]:
    """Measure one bounded navigation -> search -> exact-slice baseline flow."""

    map_stdout, map_measurement = _run_git(
        source_root,
        environment,
        ["ls-tree", "-r", "--name-only", revision],
    )
    search_stdout, search_measurement = _run_git(
        source_root,
        environment,
        ["grep", "-n", "-i", "-F", "needle", revision, "--"],
    )
    file_stdout, slice_measurement = _run_git(
        source_root,
        environment,
        ["show", f"{revision}:src/demo.py"],
    )

    paths = tuple(line.strip() for line in map_stdout.splitlines() if line.strip())
    hits = tuple(line for line in search_stdout.splitlines() if line.strip())
    file_lines = file_stdout.splitlines()
    if len(file_lines) < 2:
        raise RuntimeError("baseline exact source fixture has fewer than two lines")
    exact_slice = file_lines[1]

    if "src/demo.py" not in paths:
        raise RuntimeError("baseline repository map did not include src/demo.py")
    if not any("src/demo.py" in hit and "needle" in hit.casefold() for hit in hits):
        raise RuntimeError("baseline search did not return the expected source hit")
    if "Needle from exact source" not in exact_slice:
        raise RuntimeError("baseline source slice did not return the expected exact line")

    useful_context_ms = float(search_measurement["elapsed_ms"]) + float(
        slice_measurement["elapsed_ms"]
    )

    return {
        "provider_id": "reference.git",
        "fixture_revision": revision,
        "persistent_state_bytes": 0,
        "tool_calls_to_useful_context": 2,
        "cold_time_to_useful_context_ms": useful_context_ms,
        "warm_time_to_useful_context_ms": useful_context_ms,
        "checks": {
            "repository_map_contains_target": True,
            "search_expected_hit": True,
            "slice_exact_source": True,
            "immutable_revision_bound": True,
        },
        "commands": {
            "map": map_measurement,
            "search": search_measurement,
            "slice": slice_measurement,
        },
    }


def build_provider_comparison(
    *,
    baseline: dict[str, Any],
    candidate_measurements: list[dict[str, float | int | str]],
    candidate_state_bytes: int,
    candidate_version: str,
    fixture_revision: str,
) -> dict[str, Any]:
    """Build only like-for-like measured deltas and label other observations non-comparable."""

    measurements = {str(item["command"]): item for item in candidate_measurements}
    scan = measurements.get("scan .")
    search = measurements.get("search needle --limit 10")
    source_slice = measurements.get("slice src/demo.py --start-line 2 --end-line 2")
    if scan is None or search is None or source_slice is None:
        raise RuntimeError("candidate comparison is missing scan/search/slice measurements")

    candidate_warm_ms = float(search["elapsed_ms"]) + float(source_slice["elapsed_ms"])
    candidate_cold_ms = float(scan["elapsed_ms"]) + candidate_warm_ms

    baseline_revision = baseline.get("fixture_revision")
    if baseline_revision != fixture_revision:
        raise RuntimeError("baseline and candidate comparison revisions differ")

    return {
        "schema_version": "2",
        "fixture_kind": "deterministic-tiny-git",
        "fixture_revision": fixture_revision,
        "baseline": baseline,
        "candidate": {
            "provider_id": "candidate.projectatlas",
            "candidate_version": candidate_version,
            "fixture_revision": fixture_revision,
            "persistent_state_bytes": candidate_state_bytes,
            "tool_calls_to_useful_context": 3,
            "cold_time_to_useful_context_ms": candidate_cold_ms,
            "warm_time_to_useful_context_ms": candidate_warm_ms,
            "checks": {
                "search_expected_hit": True,
                "slice_exact_source": True,
                "immutable_revision_bound_by_harness": True,
            },
        },
        "measured_deltas_candidate_minus_baseline": {
            "cold_time_to_useful_context_ms": (
                candidate_cold_ms - float(baseline["cold_time_to_useful_context_ms"])
            ),
            "warm_time_to_useful_context_ms": (
                candidate_warm_ms - float(baseline["warm_time_to_useful_context_ms"])
            ),
            "tool_calls_to_useful_context": (3 - int(baseline["tool_calls_to_useful_context"])),
            "persistent_state_bytes": (
                candidate_state_bytes - int(baseline["persistent_state_bytes"])
            ),
        },
        "observed_but_not_normalized": {
            "baseline_search_stdout_bytes": int(baseline["commands"]["search"]["stdout_bytes"]),
            "baseline_slice_stdout_bytes": int(baseline["commands"]["slice"]["stdout_bytes"]),
            "candidate_search_stdout_bytes": int(search["stdout_bytes"]),
            "candidate_slice_stdout_bytes": int(source_slice["stdout_bytes"]),
            "note": (
                "Raw stdout sizes use provider-specific envelopes and are retained only as transport "
                "observations. They are not a model-context-size comparison."
            ),
        },
        "unmeasured_or_not_comparable": [
            "normalized model-context bytes/tokens",
            "comparable baseline-vs-candidate peak RSS",
            "representative agent first-pass success",
            "symbol/reference/dependency correctness",
            "architecture/domain/impact usefulness",
            "large-repository incremental refresh and rebuild cost",
            "dirty-workspace provider freshness",
        ],
        "decision_scope": (
            "This tiny-fixture comparison is sufficient to record real like-for-like timing, "
            "tool-call and persistent-state measurements for the #502 experimental decision, but "
            "it is not sufficient evidence to adopt ProjectAtlas as a default or to enable "
            "production source capabilities."
        ),
    }
