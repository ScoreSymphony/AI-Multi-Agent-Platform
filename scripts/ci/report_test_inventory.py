from __future__ import annotations

import json
import re
from pathlib import Path

TESTS = Path("tests")
ISSUE_NAMED = re.compile(r"^test_issue_?\d+.*\.py$")
NUMERIC_TOKEN = re.compile(r"(?:^|_)\d+(?=_|$)")


def test_modules() -> list[Path]:
    return sorted(path for path in TESTS.rglob("test_*.py") if path.is_file())


def main() -> int:
    modules = test_modules()
    root_level = [path.as_posix() for path in modules if path.parent == TESTS]
    issue_named = [path.as_posix() for path in modules if ISSUE_NAMED.fullmatch(path.name)]
    other_numeric = [
        path.as_posix()
        for path in modules
        if path.as_posix() not in issue_named and NUMERIC_TOKEN.search(path.stem)
    ]
    report = {
        "test_module_count": len(modules),
        "root_level_count": len(root_level),
        "root_level": root_level,
        "issue_named_count": len(issue_named),
        "issue_named": issue_named,
        "other_numeric_count": len(other_numeric),
        "other_numeric": other_numeric,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
