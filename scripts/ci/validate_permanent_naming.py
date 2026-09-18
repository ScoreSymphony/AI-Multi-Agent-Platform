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
ISSUE_REFERENCE = re.compile(r"(?:(?:\bissue\s+)?#\d+\b)", re.IGNORECASE)
ISSUE_STRING_TOKEN = re.compile(r"(?<![A-Za-z0-9])issue[-_]?\d+(?![A-Za-z0-9])", re.IGNORECASE)
HASH_ISSUE_REFERENCE = re.compile(r"(?<!\w)#\d+\b")
DOMAIN_ISSUE_STRING = re.compile(r"\b(?:repository|github)\s+issue\s+#\d+\b", re.IGNORECASE)
ISSUE_EVIDENCE_DIRECTORY = re.compile(r"issue_\d+", re.IGNORECASE)
WORKFLOW_ISSUE_NAME = re.compile(
    r"(?:\bissue\s+#?\d+\b|(?:^|[ _.-])issue[-_]?\d+(?:[ _.-]|$))",
    re.IGNORECASE,
)
PROVENANCE_PREFIXES = ("historical context:", "provenance:")
ISSUE_LED_CONTEXT = re.compile(r"^(?:(?:issue\\s+)?#\\d+)\\b", re.IGNORECASE)
HISTORY_ONLY_CONTEXT = re.compile(
    r"^(?:migrated|migration|tracked|follow[- ]?up|introduced|added|implemented|created|moved|split|refactored|hardened)\\b",
    re.IGNORECASE,
)
PERMANENT_ROOTS = frozenset({"src", "tests", "scripts"})
EVIDENCE_ROOT = PurePosixPath("tests/evidence")
WORKFLOW_PREFIX = PurePosixPath(".github/workflows")
STRING_LITERAL_FIXTURE_PATH = PurePosixPath("tests/unit/governance/test_permanent_naming_policy.py")
LEGACY_COMPATIBILITY_STRINGS = frozenset(
    {
        "ai-multi-agent-platform/issue-388-two-host-transport/v1",
        "ai-multi-agent-platform/issue-388-two-host-restart/v1",
        "evidence:issue388-two-host",
        "ai-multi-agent-platform/issue-562-network-probe/v1",
        "ai-multi-agent-platform/issue-562-platform-phase/v1",
        "ai-multi-agent-platform/issue-562-two-vps-private-tunnel/v1",
    }
)
LEGACY_COMPATIBILITY_PREFIXES = ("issue562:",)


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


def repository_targets(root: Path) -> tuple[ChangedPath, ...]:
    """Return every maintained path covered by the permanent-naming policy."""

    targets: list[ChangedPath] = []
    for root_name in sorted(PERMANENT_ROOTS):
        permanent_root = root / root_name
        if not permanent_root.exists():
            continue
        for local_path in sorted(path for path in permanent_root.rglob("*") if path.is_file()):
            targets.append(ChangedPath(status="M", path=local_path.relative_to(root).as_posix()))

    workflow_root = root / Path(*WORKFLOW_PREFIX.parts)
    if workflow_root.exists():
        for local_path in sorted(path for path in workflow_root.rglob("*") if path.is_file()):
            targets.append(ChangedPath(status="M", path=local_path.relative_to(root).as_posix()))
    return tuple(targets)


def _is_provenance_path(path: PurePosixPath) -> bool:
    parts = path.parts
    return (
        len(parts) >= 3
        and PurePosixPath(*parts[:2]) == EVIDENCE_ROOT
        and ISSUE_EVIDENCE_DIRECTORY.fullmatch(parts[2]) is not None
    )


def _is_workflow_path(path: PurePosixPath) -> bool:
    return WORKFLOW_PREFIX in (path, *path.parents)


def path_violations(changes: tuple[ChangedPath, ...]) -> tuple[str, ...]:
    """Reject permanent paths whose semantic name is a GitHub issue number."""

    violations: list[str] = []
    for change in changes:
        path = PurePosixPath(change.path)
        if not path.parts or _is_provenance_path(path):
            continue
        permanent_path = path.parts[0] in PERMANENT_ROOTS or _is_workflow_path(path)
        if not permanent_path:
            continue
        relevant_parts = path.parts[1:] if path.parts[0] in PERMANENT_ROOTS else path.parts
        if any(ISSUE_PATH_TOKEN.search(part) for part in relevant_parts):
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


def _issue_context_requires_rewrite(line: str) -> bool:
    """Return whether an issue reference is the meaning rather than secondary context."""

    if ISSUE_REFERENCE.search(line) is None or _provenance_line(line):
        return False
    normalized = line.strip().strip('"\'').strip()
    normalized = re.sub(r"^#+\\s+", "", normalized).strip()
    return (
        ISSUE_LED_CONTEXT.match(normalized) is not None
        or HISTORY_ONLY_CONTEXT.match(normalized) is not None
    )


def _legacy_compatibility_string(value: str) -> bool:
    return value in LEGACY_COMPATIBILITY_STRINGS or value.startswith(LEGACY_COMPATIBILITY_PREFIXES)


def _semantic_string_violations(
    path: PurePosixPath,
    tree: ast.AST,
    docstrings: tuple[ast.Constant, ...],
) -> tuple[str, ...]:
    """Reject issue-number tokens used as maintained semantic string identifiers or messages."""

    if path == STRING_LITERAL_FIXTURE_PATH:
        return ()

    docstring_ids = {id(node) for node in docstrings}
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstring_ids:
            continue
        value = node.value
        if _legacy_compatibility_string(value):
            continue
        compact_issue_token = ISSUE_STRING_TOKEN.search(value) is not None
        internal_hash_reference = (
            HASH_ISSUE_REFERENCE.search(value) is not None
            and DOMAIN_ISSUE_STRING.search(value) is None
        )
        if not compact_issue_token and not internal_hash_reference:
            continue
        violations.append(
            f"{path}:{getattr(node, 'lineno', 1)}: string value {value!r} embeds a GitHub issue "
            "number as maintained semantics"
        )
    return tuple(violations)


def source_violations(path: str, source: str) -> tuple[str, ...]:
    """Return semantic naming violations for one maintained Python source file."""

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

    docstrings = _docstring_nodes(tree)
    for node in docstrings:
        value = str(node.value)
        start = getattr(node, "lineno", 1)
        for offset, line in enumerate(value.splitlines()):
            if _issue_context_requires_rewrite(line):
                violations.append(
                    f"{path}:{start + offset}: docstring text must explain maintained behavior "
                    "without using a GitHub issue number as its subject or migration-only meaning"
                )

    violations.extend(_semantic_string_violations(repository_path, tree, docstrings))

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            if _issue_context_requires_rewrite(token.string):
                violations.append(
                    f"{path}:{token.start[0]}: comment text must explain maintained behavior "
                    "without using a GitHub issue number as its subject or migration-only meaning"
                )
    except tokenize.TokenError as exc:
        violations.append(f"{path}: cannot tokenize source for naming validation: {exc}")

    return tuple(violations)


def workflow_violations(path: str, source: str) -> tuple[str, ...]:
    """Reject concrete issue numbers used as maintained workflow/job/step names."""

    repository_path = PurePosixPath(path)
    if not _is_workflow_path(repository_path) or repository_path.suffix not in {".yml", ".yaml"}:
        return ()

    violations: list[str] = []
    in_jobs = False
    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line == "jobs:":
            in_jobs = True
            continue
        if in_jobs and line and not line.startswith((" ", "\t")):
            in_jobs = False

        if in_jobs:
            job_match = re.match(r"^  ([^\s][^:]*):\s*$", line)
            if job_match and WORKFLOW_ISSUE_NAME.search(job_match.group(1)):
                violations.append(
                    f"{path}:{line_number}: workflow job ids must describe behavior, "
                    "not a GitHub issue number"
                )

        # Only semantic workflow names are guarded here: the top-level workflow
        # display name, a job-level display name, and list-form step names. A
        # nested action input named for retained evidence is not a workflow/step name.
        name_value: str | None = None
        if match := re.match(r"^name:\s*(.+?)\s*$", line):
            name_value = match.group(1)
        elif match := re.match(r"^    name:\s*(.+?)\s*$", line):
            name_value = match.group(1)
        elif match := re.match(r"^\s*-\s*name:\s*(.+?)\s*$", line):
            name_value = match.group(1)
        if name_value is not None and WORKFLOW_ISSUE_NAME.search(name_value.strip("'\"")):
            violations.append(
                f"{path}:{line_number}: workflow and step names must describe behavior, "
                "not a GitHub issue number"
            )

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
        if not path.parts or _is_provenance_path(path):
            continue
        local_path = root / Path(*path.parts)
        if not local_path.is_file():
            continue
        if path.parts[0] in PERMANENT_ROOTS and path.suffix == ".py":
            violations.extend(
                source_violations(change.path, local_path.read_text(encoding="utf-8"))
            )
        elif _is_workflow_path(path) and path.suffix in {".yml", ".yaml"}:
            violations.extend(
                workflow_violations(change.path, local_path.read_text(encoding="utf-8"))
            )
    return tuple(violations)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reject GitHub issue numbers used as permanent code/test/workflow semantics "
            "in a diff or across the maintained repository tree."
        )
    )
    parser.add_argument("--base", help="Base commit SHA/ref for diff-scoped validation")
    parser.add_argument("--head", help="Head commit SHA/ref for diff-scoped validation")
    parser.add_argument(
        "--full-tree",
        action="store_true",
        help="Validate all maintained source/test/script/workflow paths instead of a diff.",
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.repository_root.resolve()
    if args.full_tree:
        if args.base is not None or args.head is not None:
            parser.error("--full-tree cannot be combined with --base/--head")
        changes = repository_targets(root)
    else:
        if args.base is None or args.head is None:
            parser.error("--base and --head are required unless --full-tree is used")
        changes = changed_targets(git_name_status(args.base, args.head))

    violations = validate_changed_tree(root, changes)
    if not violations:
        return 0

    print("Permanent naming policy violations:")
    for violation in violations:
        print(f"- {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
