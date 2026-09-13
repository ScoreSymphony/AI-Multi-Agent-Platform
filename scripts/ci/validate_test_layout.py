from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath

ISSUE_TEST_NAME = re.compile(r"^test_issue_?\d+.*\.py$")


@dataclass(frozen=True)
class ChangedPath:
    status: str
    path: str


def changed_targets(name_status: str) -> tuple[ChangedPath, ...]:
    """Return newly introduced target paths from git --name-status output.

    Existing legacy files may still be modified while #722 migrates them, so
    modified/deleted paths are intentionally ignored. Rename/copy records use
    their destination path because that is the newly introduced repository name.
    """

    changes: list[ChangedPath] = []
    for raw_line in name_status.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split("\t")
        status = fields[0]
        kind = status[:1]
        if kind == "A" and len(fields) == 2:
            changes.append(ChangedPath(status=status, path=fields[1]))
        elif kind in {"C", "R"} and len(fields) == 3:
            changes.append(ChangedPath(status=status, path=fields[2]))
    return tuple(changes)


def layout_violations(changes: tuple[ChangedPath, ...]) -> tuple[str, ...]:
    violations: list[str] = []
    for change in changes:
        path = PurePosixPath(change.path)
        if not path.parts or path.parts[0] != "tests" or path.suffix != ".py":
            continue

        if len(path.parts) == 2 and path.name.startswith("test_"):
            violations.append(
                f"{change.path}: new ordinary tests must live in a canonical suite directory",
            )

        if ISSUE_TEST_NAME.fullmatch(path.name):
            violations.append(
                f"{change.path}: new test filenames must describe behavior, not an issue number",
            )
    return tuple(violations)


def git_name_status(base: str, head: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--find-renames",
            "--diff-filter=ACR",
            base,
            head,
            "--",
            "tests",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject newly introduced root-level or issue-numbered test modules.",
    )
    parser.add_argument("--base", required=True, help="Base commit SHA/ref")
    parser.add_argument("--head", required=True, help="Head commit SHA/ref")
    args = parser.parse_args()

    changes = changed_targets(git_name_status(args.base, args.head))
    violations = layout_violations(changes)
    if not violations:
        return 0

    print("Test layout policy violations:")
    for violation in violations:
        print(f"- {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
