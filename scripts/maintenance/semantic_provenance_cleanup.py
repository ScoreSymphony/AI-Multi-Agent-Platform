from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "9f3b29d2bd845512d590592881292fe88318983b"


def _changed_python_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{BASE}...HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        ROOT / item
        for item in completed.stdout.splitlines()
        if item.startswith("src/") and item.endswith(".py") and (ROOT / item).is_file()
    ]


def _semanticize_issue_references(text: str) -> str:
    # Permanent source names/docstrings should describe behavior, not historical work items.
    # Remove only provenance tokens; never normalize whitespace or alter Python structure.
    text = re.sub(r"\bIssue[ \t]+#\d+[ \t]*", "", text)
    text = re.sub(
        r"\bissue[ \t]+#\d+\b",
        "the owning subsystem",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<![A-Za-z0-9_])#\d+\b", "", text)
    return text


def main() -> int:
    changed = 0
    for path in _changed_python_files():
        before = path.read_text(encoding="utf-8")
        after = _semanticize_issue_references(before)
        if after != before:
            compile(after, str(path), "exec")
            path.write_text(after, encoding="utf-8")
            changed += 1

    print(f"semantic_provenance_files_changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
