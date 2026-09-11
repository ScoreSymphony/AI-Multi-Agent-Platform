"""Run the deterministic #800 SkillSpector corpus and write machine-readable evidence.

This script is evaluation-only. It records scanner behavior; it does not turn a scanner
result into platform trust, approval, installation or activation state.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median
import tempfile
from time import perf_counter
from typing import Any, Mapping

from experiments.skillspector.fixtures import write_fixture_corpus
from experiments.skillspector.normalize import normalize_report
from experiments.skillspector.runner import DEFAULT_IMAGE, PINNED_REVISION, PINNED_VERSION, evaluate


def _finding_signature(evidence: Mapping[str, Any]) -> str:
    findings = evidence.get("findings")
    canonical = json.dumps(findings, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def _run_once(
    fixture: Path,
    *,
    image: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        result = evaluate(
            fixture,
            runtime="docker",
            image=image,
            allow_local_process=False,
            timeout_seconds=timeout_seconds,
        )
        elapsed = perf_counter() - started
        report = result.get("report")
        normalized: dict[str, Any] | None = None
        if isinstance(report, Mapping):
            evidence = normalize_report(
                report,
                provider_version=PINNED_VERSION,
                provider_revision=PINNED_REVISION,
                mode="static_no_llm_network_none",
                candidate_digest=str(result["candidate_digest"]),
                process_ok=result.get("process_ok") is True,
            )
            normalized = evidence.to_dict()
        return {
            "elapsed_seconds": elapsed,
            "exception": None,
            "process": {
                "returncode": result.get("returncode"),
                "process_ok": result.get("process_ok"),
                "isolation": result.get("isolation"),
                "stderr": result.get("stderr"),
                "stdout": result.get("stdout"),
            },
            "report": report,
            "normalized_evidence": normalized,
            "finding_signature": _finding_signature(normalized) if normalized else None,
        }
    except Exception as exc:  # benchmark evidence must preserve failures rather than hide them
        return {
            "elapsed_seconds": perf_counter() - started,
            "exception": f"{type(exc).__name__}: {exc}",
            "process": None,
            "report": None,
            "normalized_evidence": None,
            "finding_signature": None,
        }


def _summarize(name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(run["elapsed_seconds"]) for run in runs]
    process_ok = [
        bool(run.get("process") and run["process"].get("process_ok") is True) for run in runs
    ]
    normalized = [run.get("normalized_evidence") for run in runs]
    statuses = [
        evidence.get("status") if isinstance(evidence, Mapping) else "no_evidence"
        for evidence in normalized
    ]
    finding_counts = [
        len(evidence.get("findings", [])) if isinstance(evidence, Mapping) else None
        for evidence in normalized
    ]
    signatures = [run.get("finding_signature") for run in runs if run.get("finding_signature")]
    return {
        "fixture": name,
        "runs": len(runs),
        "process_ok_runs": sum(process_ok),
        "exceptions": [run["exception"] for run in runs if run.get("exception")],
        "status_counts": dict(Counter(statuses)),
        "finding_counts": finding_counts,
        "finding_signature_stable": len(set(signatures)) <= 1 if signatures else None,
        "mean_seconds": mean(durations),
        "median_seconds": median(durations),
        "min_seconds": min(durations),
        "max_seconds": max(durations),
    }


def run_benchmark(
    *,
    output_dir: Path,
    repeats: int,
    image: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "runs"
    raw_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="skillspector-corpus-") as temporary:
        corpus = Path(temporary)
        write_fixture_corpus(corpus)
        fixture_dirs = sorted(path.parent for path in corpus.rglob("SKILL.md"))

        all_runs: dict[str, list[dict[str, Any]]] = {}
        for fixture in fixture_dirs:
            name = fixture.relative_to(corpus).as_posix()
            runs: list[dict[str, Any]] = []
            for repeat in range(1, repeats + 1):
                result = _run_once(fixture, image=image, timeout_seconds=timeout_seconds)
                runs.append(result)
                target = raw_dir / f"{name.replace('/', '__')}--run-{repeat}.json"
                target.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
            all_runs[name] = runs

    summary: dict[str, Any] = {
        "schema_version": 1,
        "upstream": {
            "project": "NVIDIA/SkillSpector",
            "version": PINNED_VERSION,
            "revision": PINNED_REVISION,
            "image": image,
        },
        "mode": "static_no_llm_network_none",
        "repeats": repeats,
        "fixtures": {name: _summarize(name, runs) for name, runs in all_runs.items()},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    summary = run_benchmark(
        output_dir=args.output,
        repeats=args.repeats,
        image=args.image,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))

    # A benchmark is useful even when the scanner reports degraded evidence or misses a
    # planted behavior. Fail only when no fixture produced a runnable scanner result at all.
    runnable = sum(
        int(fixture["process_ok_runs"] > 0) for fixture in summary["fixtures"].values()
    )
    return 0 if runnable > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
