#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ISSUE_IDENTIFIER = re.compile(r"(?:^|_)issue_?\d+(?:_|$)", re.IGNORECASE)
ISSUE_PATH_TOKEN = re.compile(r"(?:^|[._-])issue[-_]?\d+(?:[._-]|$)", re.IGNORECASE)
ISSUE_REFERENCE = re.compile(r"\bissue\s+#\d+\b", re.IGNORECASE)
PROVENANCE_PREFIXES = ("historical context:", "provenance:")
PERMANENT_ROOTS = frozenset({"src", "tests", "scripts"})
PROVENANCE_PATH_PREFIXES = (PurePosixPath("tests/evidence"),)


@dataclass(frozen=True, slots=True)
class ChangedPath:
    status: str
    path: str


def changed_targets(name_status: str) -> tuple[ChangedPath, ...]:
    """Return current target paths for added, modified, copied and renamed files."""

    changes: list[ChangedPath] = []
    for raw_line in name_status.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split("\t")
        status = fields[0]
        kind = status[:1]
        if kind in {"A", "M"} and len(fields) == 2:
            changes.append(ChangedPath(status=status, path=fields[1]))
        elif kind in {"C", "R"} and len(fields) == 3:
            changes.append(ChangedPath(status=status, path=fields[2]))
    return tuple(changes)


def _is_provenance_path(path: PurePosixPath) -> bool:
    return any(path == prefix or prefix in path.parents for prefix in PROVENANCE_PATH_PREFIXES)


def path_violations(changes: tuple[ChangedPath, ...]) -> tuple[str, ...]:
    """Reject new permanent paths whose semantic name is a GitHub issue number."""

    violations: list[str] = []
    for change in changes:
        path = PurePosixPath(change.path)
        if not path.parts or path.parts[0] not in PERMANENT_ROOTS or _is_provenance_path(path):
            continue
        if any(ISSUE_PATH_TOKEN.search(part) for part in path.parts[1:]):
            violations.append(
                f"{change.path}: permanent paths must describe behavior, not a GitHub issue number"
            )
    return tuple(violations)


def _identifier_names(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append((node.name, node.lineno))
        elif isinstance(node, ast.arg):
            names.append((node.arg, node.lineno))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute):
            names.append((node.attr, node.lineno))
        elif isinstance(node, ast.alias) and node.asname:
            names.append((node.asname, getattr(node, "lineno", 1)))
    return tuple(names)


def _docstring_nodes(tree: ast.AST) -> tuple[ast.Constant, ...]:
    nodes: list[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", ())
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            nodes.append(first.value)
    return tuple(nodes)


def _provenance_line(line: str) -> bool:
    normalized = line.strip().lstrip("#").strip().lower()
    return normalized.startswith(PROVENANCE_PREFIXES)


def source_violations(path: str, source: str) -> tuple[str, ...]:
    """Return semantic naming violations for one changed Python source file."""

    repository_path = PurePosixPath(path)
    if repository_path.suffix != ".py" or _is_provenance_path(repository_path):
        return ()

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return (
            f"{path}:{exc.lineno or 1}: cannot validate naming because Python syntax is invalid",
        )

    violations: list[str] = []
    for name, line in _identifier_names(tree):
        if ISSUE_IDENTIFIER.search(name):
            violations.append(
                f"{path}:{line}: identifier {name!r} must describe behavior, "
                "not a GitHub issue number"
            )

    for node in _docstring_nodes(tree):
        value = str(node.value)
        start = getattr(node, "lineno", 1)
        for offset, line in enumerate(value.splitlines()):
            if ISSUE_REFERENCE.search(line) and not _provenance_line(line):
                violations.append(
                    f"{path}:{start + offset}: issue provenance in docstrings must be secondary "
                    "'Historical context:' or 'Provenance:' text"
                )

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            if ISSUE_REFERENCE.search(token.string) and not _provenance_line(token.string):
                violations.append(
                    f"{path}:{token.start[0]}: issue provenance in comments must be secondary "
                    "'Historical context:' or 'Provenance:' text"
                )
    except tokenize.TokenError as exc:
        violations.append(f"{path}: cannot tokenize source for naming validation: {exc}")

    return tuple(violations)


def git_name_status(base: str, head: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--find-renames",
            "--diff-filter=ACMR",
            base,
            head,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def validate_changed_tree(root: Path, changes: tuple[ChangedPath, ...]) -> tuple[str, ...]:
    violations = list(path_violations(changes))
    for change in changes:
        path = PurePosixPath(change.path)
        if not path.parts or path.parts[0] not in PERMANENT_ROOTS or path.suffix != ".py":
            continue
        if _is_provenance_path(path):
            continue
        local_path = root / Path(*path.parts)
        if not local_path.is_file():
            continue
        violations.extend(source_violations(change.path, local_path.read_text(encoding="utf-8")))
    return tuple(violations)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reject GitHub issue numbers used as permanent code/test semantics in changed files."
        )
    )
    parser.add_argument("--base", required=True, help="Base commit SHA/ref")
    parser.add_argument("--head", required=True, help="Head commit SHA/ref")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    changes = changed_targets(git_name_status(args.base, args.head))
    violations = validate_changed_tree(args.repository_root.resolve(), changes)
    if not violations:
        return 0

    print("Permanent naming policy violations:")
    for violation in violations:
        print(f"- {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
