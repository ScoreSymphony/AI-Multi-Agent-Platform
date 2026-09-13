from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path

import plan_test_migration_722 as plan

STAGING = Path(".test-migration-722-staging")
MANIFEST = Path("docs/TEST_MIGRATION_722_FINAL.md")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def build_safe_plan() -> tuple[dict[Path, Path], dict[str, str]]:
    modules = plan.all_modules()
    candidates = [path for path in modules if plan.is_candidate(path)]
    candidate_set = set(candidates)
    moves: dict[Path, Path] = {}
    residual: dict[str, str] = {}

    for path in candidates:
        domains = plan.mixed_domains(path)
        if domains:
            residual[path.as_posix()] = (
                "mixed: substantial test-function responsibilities span " + ", ".join(domains)
            )
            continue
        moves[path] = plan.desired_destination(path)

    groups: dict[Path, list[Path]] = defaultdict(list)
    for source, destination in moves.items():
        groups[destination].append(source)

    for destination, sources in groups.items():
        occupied = destination.exists() and destination not in candidate_set
        if len(sources) > 1 or occupied:
            reason = (
                f"collision: {destination.as_posix()}"
                if len(sources) > 1
                else f"existing destination: {destination.as_posix()}"
            )
            for source in sources:
                residual[source.as_posix()] = reason
                moves.pop(source, None)

    return moves, residual


def shift_file_ancestor_references(path: Path, depth_delta: int) -> None:
    if depth_delta <= 0:
        return
    text = path.read_text(encoding="utf-8")

    def shift_parents(match: re.Match[str]) -> str:
        return f"Path(__file__).resolve().parents[{int(match.group(1)) + depth_delta}]"

    text = re.sub(
        r"Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]",
        shift_parents,
        text,
    )
    text = text.replace(
        "Path(__file__).resolve().parent",
        f"Path(__file__).resolve().parents[{depth_delta}]",
    )
    path.write_text(text, encoding="utf-8")


def rewrite_exact_paths(mapping: dict[str, str]) -> int:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    changed = 0
    excluded = {
        "scripts/ci/apply_test_migration_722.py",
        "scripts/ci/plan_test_migration_722.py",
    }
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        file_path = Path(raw.decode("utf-8"))
        posix = file_path.as_posix()
        if posix in excluded or not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "tests/" not in text:
            continue
        updated = text
        for old, new in mapping.items():
            if old in updated:
                updated = updated.replace(old, new)
        if updated != text:
            file_path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def write_manifest(moves: dict[Path, Path], residual: dict[str, str], reference_updates: int) -> None:
    lines = [
        "# Final test migration for #722",
        "",
        "This document records the final rest-cohort migration inventory. Historical issue provenance",
        "is retained by Git rename history rather than issue-numbered filenames.",
        "",
        "## Baseline",
        "",
        "- Baseline test modules: 892",
        "- Baseline root-level ordinary test modules: 480",
        "- Baseline issue-numbered test modules: 482",
        "- Safe moves/renames applied in the first pass: " + str(len(moves)),
        "- Tracked text files with exact-path references updated: " + str(reference_updates),
        "",
        "## First-pass residuals",
        "",
        "These files are deliberately not guessed into a destination. Mixed files require function-level",
        "classification/splitting; collisions require behavior-specific naming rather than reusing an issue",
        "number or overwriting another test module.",
        "",
    ]
    if residual:
        for source, reason in sorted(residual.items()):
            lines.append(f"- `{source}` — {reason}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Safe move map",
            "",
        ]
    )
    for source, destination in sorted(moves.items(), key=lambda item: item[0].as_posix()):
        lines.append(f"- `{source.as_posix()}` → `{destination.as_posix()}`")
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    moves, residual = build_safe_plan()
    if not moves:
        raise SystemExit("No safe migration moves were planned")

    STAGING.mkdir(exist_ok=False)
    staged: list[tuple[Path, Path, Path]] = []
    for index, (source, destination) in enumerate(
        sorted(moves.items(), key=lambda item: item[0].as_posix())
    ):
        temporary = STAGING / f"{index:04d}.py"
        run("git", "mv", source.as_posix(), temporary.as_posix())
        staged.append((source, temporary, destination))

    mapping: dict[str, str] = {}
    for source, temporary, destination in staged:
        destination.parent.mkdir(parents=True, exist_ok=True)
        run("git", "mv", temporary.as_posix(), destination.as_posix())
        mapping[source.as_posix()] = destination.as_posix()
        old_parent_depth = len(source.parent.parts)
        new_parent_depth = len(destination.parent.parts)
        shift_file_ancestor_references(destination, new_parent_depth - old_parent_depth)

    STAGING.rmdir()
    reference_updates = rewrite_exact_paths(mapping)
    write_manifest(moves, residual, reference_updates)

    print(f"Applied {len(moves)} safe moves/renames")
    print(f"Updated exact-path references in {reference_updates} tracked text files")
    print(f"Left {len(residual)} deliberate residual files for the split/collision pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
