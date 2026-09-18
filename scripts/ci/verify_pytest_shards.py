from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ("-m", "not unit", "tests")
SHARDS: dict[str, tuple[str, ...]] = {
    "contract-architecture-release": (
        "-m",
        "not unit",
        "tests/architecture",
        "tests/contract",
        "tests/release",
    ),
    "integration": ("-m", "not unit", "tests/integration"),
    "system-regression": (
        "-m",
        "not unit",
        "tests/e2e",
        "tests/performance",
        "tests/regression",
    ),
}


def _collect(arguments: Sequence[str]) -> set[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "--collect-only",
        "-q",
        *arguments,
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)

    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }


def compare_collections(
    baseline: set[str], shards: dict[str, set[str]]
) -> tuple[set[str], set[str], set[str]]:
    combined: set[str] = set()
    duplicates: set[str] = set()
    for nodeids in shards.values():
        duplicates.update(combined.intersection(nodeids))
        combined.update(nodeids)

    return baseline - combined, combined - baseline, duplicates


def main() -> int:
    baseline = _collect(BASELINE)
    shards = {name: _collect(arguments) for name, arguments in SHARDS.items()}
    missing, unexpected, duplicates = compare_collections(baseline, shards)

    print(f"serial non-unit collection: {len(baseline)} tests")
    for name, nodeids in shards.items():
        print(f"{name}: {len(nodeids)} tests")
    print(f"sharded union: {len(set().union(*shards.values()))} tests")

    if missing:
        print("missing from shards:", file=sys.stderr)
        for nodeid in sorted(missing):
            print(f"  {nodeid}", file=sys.stderr)
    if unexpected:
        print("unexpected in shards:", file=sys.stderr)
        for nodeid in sorted(unexpected):
            print(f"  {nodeid}", file=sys.stderr)
    if duplicates:
        print("duplicated across shards:", file=sys.stderr)
        for nodeid in sorted(duplicates):
            print(f"  {nodeid}", file=sys.stderr)

    return 1 if missing or unexpected or duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())
