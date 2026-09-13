from __future__ import annotations

import re
import subprocess
from pathlib import Path

import plan_test_migration_722 as plan

STAGING = Path(".test-migration-722-staging")
MANIFEST = Path("docs/TEST_MIGRATION_722_FINAL.md")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


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
    # Do not match the `parent` prefix inside `.parents[...]`.
    text = re.sub(
        r"Path\(__file__\)\.resolve\(\)\.parent(?!s\[)",
        f"Path(__file__).resolve().parents[{depth_delta}]",
        text,
    )
    path.write_text(text, encoding="utf-8")


def rewrite_exact_paths(mapping: dict[str, str]) -> int:
    completed = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
    changed = 0
    excluded = {
        "scripts/ci/apply_test_migration_722.py",
        "scripts/ci/plan_test_migration_722.py",
    }
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        file_path = Path(raw.decode("utf-8"))
        if file_path.as_posix() in excluded or not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "tests/" not in text:
            continue
        updated = text
        for old, new in mapping.items():
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
        f"- Safe moves/renames applied in the first pass: {len(moves)}",
        f"- Tracked text files with exact-path references updated: {reference_updates}",
        "",
        "## First-pass residuals",
        "",
        "These files are deliberately not guessed into a destination. Mixed files require function-level",
        "classification/splitting; exact destination collisions require behavior-specific naming rather",
        "than reusing an issue number or overwriting another test module.",
        "",
    ]
    lines.extend(
        f"- `{source}` — {reason}" for source, reason in sorted(residual.items())
    )
    lines.extend(["", "## Safe move map", ""])
    lines.extend(
        f"- `{source.as_posix()}` → `{destination.as_posix()}`"
        for source, destination in sorted(moves.items(), key=lambda item: item[0].as_posix())
    )
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    moves, residual, _mixed = plan.build_plan()
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
        depth_delta = len(destination.parent.parts) - len(source.parent.parts)
        shift_file_ancestor_references(destination, depth_delta)

    STAGING.rmdir()
    reference_updates = rewrite_exact_paths(mapping)
    write_manifest(moves, residual, reference_updates)

    print(f"Applied {len(moves)} safe moves/renames")
    print(f"Updated exact-path references in {reference_updates} tracked text files")
    print(f"Left {len(residual)} deliberate residual files for the split/collision pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
