#!/usr/bin/env python3
"""Run the final #502 ProjectAtlas pilot plus a real deterministic Git baseline comparison."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from issue502_baseline_comparison import (
    build_provider_comparison,
    measure_git_reference_baseline,
)
from issue502_projectatlas_pilot import (
    PINNED_VERSION,
    _make_fixture,
    _make_read_only,
    _provider_environment,
    _restore_writable,
    run_pilot,
)

_DETERMINISTIC_GIT_DATE = "2000-01-01T00:00:00Z"


def run_comparison(binary: Path, no_network_wrapper: Path) -> dict[str, Any]:
    """Run candidate evidence and compare it with the same immutable tiny Git fixture."""

    previous_author_date = os.environ.get("GIT_AUTHOR_DATE")
    previous_committer_date = os.environ.get("GIT_COMMITTER_DATE")
    os.environ["GIT_AUTHOR_DATE"] = _DETERMINISTIC_GIT_DATE
    os.environ["GIT_COMMITTER_DATE"] = _DETERMINISTIC_GIT_DATE
    try:
        candidate_report = run_pilot(binary, no_network_wrapper)

        with tempfile.TemporaryDirectory(prefix="issue502-baseline-") as temporary:
            root = Path(temporary)
            source_root = root / "source"
            state_root = root / "baseline-state"
            source_root.mkdir()
            state_root.mkdir()
            revision = _make_fixture(source_root)
            if revision != candidate_report["fixture_revision"]:
                raise RuntimeError(
                    "deterministic baseline fixture revision differs from ProjectAtlas fixture"
                )
            environment = _provider_environment(state_root)
            _make_read_only(source_root)
            try:
                baseline = measure_git_reference_baseline(
                    source_root=source_root,
                    revision=revision,
                    environment=environment,
                )
            finally:
                _restore_writable(source_root)

        comparison = build_provider_comparison(
            baseline=baseline,
            candidate_measurements=candidate_report["commands"],
            candidate_state_bytes=int(candidate_report["provider_state_bytes"]),
            candidate_version=PINNED_VERSION,
            fixture_revision=revision,
        )
        candidate_report["schema_version"] = "3"
        candidate_report["comparison"] = comparison
        candidate_report["decision"] = {
            "status": "experimental/deferred",
            "default_provider": False,
            "source_capabilities_enabled": False,
            "reason": (
                "The contained pilot and real tiny-fixture comparison are sufficient to retain "
                "ProjectAtlas as an evaluated optional candidate. They do not provide the "
                "representative correctness, dirty-workspace, large-repository resource, and "
                "version-pinned source-output normalization evidence required for production "
                "source capabilities or default adoption."
            ),
        }
        return candidate_report
    finally:
        if previous_author_date is None:
            os.environ.pop("GIT_AUTHOR_DATE", None)
        else:
            os.environ["GIT_AUTHOR_DATE"] = previous_author_date
        if previous_committer_date is None:
            os.environ.pop("GIT_COMMITTER_DATE", None)
        else:
            os.environ["GIT_COMMITTER_DATE"] = previous_committer_date


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--no-network-wrapper", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    report = run_comparison(args.binary, args.no_network_wrapper)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
