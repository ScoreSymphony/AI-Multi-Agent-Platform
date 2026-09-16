from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "9f3b29d2bd845512d590592881292fe88318983b"
ISSUE_REF = re.compile(r"#\d+")

_RUNTIME_LINES: dict[str, set[int]] = {
    "src/ai_multi_agent_platform/adapters/skillspector.py": {74},
    "src/ai_multi_agent_platform/planning/evidence.py": {162, 187},
    "src/ai_multi_agent_platform/security/authentication_hardening.py": {108},
    "src/ai_multi_agent_platform/upgrade/preflight.py": {379, 388},
}


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
        if item.endswith(".py") and (ROOT / item).is_file()
    ]


def _clean_runtime_references(path: Path, lines: list[str]) -> None:
    relative = path.relative_to(ROOT).as_posix()
    for line_number in _RUNTIME_LINES.get(relative, set()):
        index = line_number - 1
        if 0 <= index < len(lines):
            lines[index] = re.sub(r"#\d+\s*", "", lines[index])


def _semantic_docstring(line: str) -> list[str] | None:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    quote = '"""' if stripped.startswith('"""') else "'''" if stripped.startswith("'''") else None
    if quote is None or stripped.count(quote) < 2 or not ISSUE_REF.search(stripped):
        return None

    inner = stripped[len(quote) : stripped.rfind(quote)].strip()
    refs = ISSUE_REF.findall(inner)
    if not refs:
        return None
    semantic = re.sub(
        r"\s*(?:(?:for|from|in)\s+)?(?:issue\s*)?#\d+[.,;:]?",
        "",
        inner,
        flags=re.IGNORECASE,
    ).strip()
    semantic = re.sub(r"\s{2,}", " ", semantic).rstrip(" .") + "."
    provenance = ", ".join(refs)
    return [
        f"{indent}{quote}{semantic}\n",
        "\n",
        f"{indent}Provenance: historical GitHub issue {provenance}.\n",
        f"{indent}{quote}\n",
    ]


def main() -> int:
    changed = 0
    for path in _changed_python_files():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        before = "".join(lines)
        _clean_runtime_references(path, lines)

        output: list[str] = []
        for line in lines:
            replacement = _semantic_docstring(line)
            output.extend(replacement if replacement is not None else [line])
        after = "".join(output)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed += 1

    print(f"semantic_provenance_files_changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
