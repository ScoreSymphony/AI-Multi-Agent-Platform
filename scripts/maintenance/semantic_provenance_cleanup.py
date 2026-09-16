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


def _clean_semantic_text(text: str) -> str:
    text = re.sub(r"\bIssue\s+#\d+\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+for\s+issue\s+#\d+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bissue\s+#\d+\b", "the owning subsystem", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _semanticize_docstrings(lines: list[str]) -> list[str]:
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        quote = (
            '"""'
            if stripped.startswith('"""')
            else "'''"
            if stripped.startswith("'''")
            else None
        )
        if quote is None:
            output.append(line)
            index += 1
            continue

        if stripped.count(quote) >= 2:
            if not ISSUE_REF.search(stripped):
                output.append(line)
                index += 1
                continue
            inner = stripped[len(quote) : stripped.rfind(quote)].strip()
            refs = ISSUE_REF.findall(inner)
            semantic = re.sub(
                r"\s*(?:(?:for|from|in)\s+)?(?:issue\s*)?#\d+[.,;:]?",
                "",
                inner,
                flags=re.IGNORECASE,
            ).strip()
            semantic = re.sub(r"\s{2,}", " ", semantic).rstrip(" .") + "."
            if semantic and semantic[0].islower():
                semantic = semantic[0].upper() + semantic[1:]
            output.extend(
                [
                    f"{indent}{quote}{semantic}\n",
                    "\n",
                    f"{indent}Provenance: historical GitHub issue {', '.join(refs)}.\n",
                    f"{indent}{quote}\n",
                ]
            )
            index += 1
            continue

        end = index + 1
        while end < len(lines) and quote not in lines[end]:
            end += 1
        if end >= len(lines):
            output.append(line)
            index += 1
            continue

        block = lines[index : end + 1]
        joined = "".join(block)
        refs = ISSUE_REF.findall(joined)
        if not refs:
            output.extend(block)
            index = end + 1
            continue

        cleaned: list[str] = []
        for block_line in block[:-1]:
            cleaned.append(_clean_semantic_text(block_line))
        if cleaned and cleaned[0].lstrip().startswith(quote):
            first = cleaned[0]
            first_indent = first[: len(first) - len(first.lstrip())]
            first_body = first.lstrip()[len(quote) :]
            cleaned[0] = f"{first_indent}{quote}{first_body}"
        if cleaned and cleaned[-1].strip():
            cleaned.append("\n")
        cleaned.append(f"{indent}Provenance: historical GitHub issue {', '.join(refs)}.\n")
        cleaned.append(block[-1])
        output.extend(cleaned)
        index = end + 1

    return output


def main() -> int:
    changed = 0
    for path in _changed_python_files():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        before = "".join(lines)
        _clean_runtime_references(path, lines)
        output = _semanticize_docstrings(lines)
        after = "".join(output)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed += 1

    print(f"semantic_provenance_files_changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
