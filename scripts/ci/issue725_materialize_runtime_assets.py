#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

MATERIALIZE_COMMAND = "python scripts/ci/issue725_materialize_runtime_assets.py"


@dataclass(frozen=True, slots=True)
class RuntimeAsset:
    canonical: Path
    generated: Path


ASSETS = (
    RuntimeAsset(
        Path("schemas/backup-manifest-v1.schema.json"),
        Path("src/ai_multi_agent_platform/backup/backup-manifest-v1.schema.json"),
    ),
    RuntimeAsset(
        Path("release/compatibility.json"),
        Path("src/ai_multi_agent_platform/release/compatibility.json"),
    ),
)


def default_repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def check_assets(root: Path) -> list[str]:
    problems: list[str] = []
    for asset in ASSETS:
        source = root / asset.canonical
        target = root / asset.generated
        if not source.is_file():
            problems.append(f"missing canonical runtime asset: {asset.canonical}")
            continue
        if not target.is_file():
            problems.append(
                f"missing generated runtime asset: {asset.generated} "
                f"(run {MATERIALIZE_COMMAND})"
            )
            continue
        if source.read_bytes() != target.read_bytes():
            problems.append(
                f"stale generated runtime asset: {asset.generated} differs from "
                f"{asset.canonical} (run {MATERIALIZE_COMMAND})"
            )
    return problems


def materialize_assets(root: Path) -> list[Path]:
    changed: list[Path] = []
    for asset in ASSETS:
        source = root / asset.canonical
        target = root / asset.generated
        if not source.is_file():
            raise FileNotFoundError(f"missing canonical runtime asset: {asset.canonical}")
        payload = source.read_bytes()
        if target.is_file() and target.read_bytes() == payload:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        changed.append(asset.generated)
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize package-local runtime assets from canonical sources."
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=default_repository_root(),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.repository_root.resolve()
    if args.check:
        problems = check_assets(root)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print("Canonical runtime assets are materialized and in sync.")
        return 0

    try:
        changed = materialize_assets(root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if changed:
        for path in changed:
            print(f"materialized {path}")
    else:
        print("Canonical runtime assets are already materialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
