from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / "scripts" / "ci" / "broad_exception_audit.py"


def _scan() -> list[dict[str, object]]:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCANNER),
            "--repository-root",
            str(ROOT),
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _handlers(source: str) -> dict[int, ast.ExceptHandler]:
    tree = ast.parse(source)
    return {
        node.lineno: node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
    }


def _redact_handler_segment(segment: str, name: str) -> str:
    segment = re.sub(
        rf"\bstr\(\s*{re.escape(name)}\s*\)",
        f"type({name}).__name__",
        segment,
    )
    segment = re.sub(
        rf"\{{\s*{re.escape(name)}\s*\}}",
        f"{{type({name}).__name__}}",
        segment,
    )
    return segment


def main() -> int:
    risky = [
        item
        for item in _scan()
        if item["source_class"] == "production" and item["diagnostic_text_risk"]
    ]
    by_file: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in risky:
        by_file[str(item["file"])].append(item)

    changed = 0
    for relative, items in by_file.items():
        path = ROOT / relative
        source = path.read_text(encoding="utf-8")
        nodes = _handlers(source)
        lines = source.splitlines(keepends=True)
        for item in sorted(items, key=lambda value: int(value["line"]), reverse=True):
            line = int(item["line"])
            node = nodes.get(line)
            if node is None or not node.name or node.end_lineno is None:
                raise RuntimeError(f"cannot locate diagnostic handler {relative}:{line}")
            start = node.lineno - 1
            end = node.end_lineno
            segment = "".join(lines[start:end])
            redacted = _redact_handler_segment(segment, node.name)
            if redacted == segment:
                raise RuntimeError(f"no diagnostic replacement made for {relative}:{line}")
            lines[start:end] = [redacted]
            changed += 1
        path.write_text("".join(lines), encoding="utf-8")

    remaining = [
        item
        for item in _scan()
        if item["source_class"] == "production" and item["diagnostic_text_risk"]
    ]
    print(f"redacted_handlers={changed} remaining_diagnostic_text_risk={len(remaining)}")
    for item in remaining:
        print(f"{item['file']}:{item['line']} {item['scope']}")
    return int(bool(remaining))


if __name__ == "__main__":
    raise SystemExit(main())
