#!/usr/bin/env python3
"""Inventory Python module/function maintainability signals and guard new extremes."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Thresholds:
    module_review_lines: int
    module_extreme_lines: int
    function_review_lines: int
    function_extreme_lines: int
    function_review_complexity: int
    function_extreme_complexity: int


@dataclass(frozen=True, slots=True)
class Exemption:
    kind: str
    path: str
    symbol: str | None
    reason: str

    @property
    def key(self) -> tuple[str, str, str | None]:
        return (self.kind, self.path, self.symbol)


@dataclass(frozen=True, slots=True)
class Configuration:
    thresholds: Thresholds
    exemptions: Mapping[tuple[str, str, str | None], Exemption]


def _positive_int(raw: object, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return raw


def load_configuration(path: Path) -> Configuration:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    thresholds_raw = raw.get("thresholds")
    if not isinstance(thresholds_raw, dict):
        raise ValueError("maintainability config requires a [thresholds] table")

    thresholds = Thresholds(
        module_review_lines=_positive_int(
            thresholds_raw.get("module_review_lines"), "module_review_lines"
        ),
        module_extreme_lines=_positive_int(
            thresholds_raw.get("module_extreme_lines"), "module_extreme_lines"
        ),
        function_review_lines=_positive_int(
            thresholds_raw.get("function_review_lines"), "function_review_lines"
        ),
        function_extreme_lines=_positive_int(
            thresholds_raw.get("function_extreme_lines"), "function_extreme_lines"
        ),
        function_review_complexity=_positive_int(
            thresholds_raw.get("function_review_complexity"),
            "function_review_complexity",
        ),
        function_extreme_complexity=_positive_int(
            thresholds_raw.get("function_extreme_complexity"),
            "function_extreme_complexity",
        ),
    )
    if thresholds.module_review_lines >= thresholds.module_extreme_lines:
        raise ValueError("module review threshold must be below the extreme threshold")
    if thresholds.function_review_lines >= thresholds.function_extreme_lines:
        raise ValueError("function line review threshold must be below the extreme threshold")
    if thresholds.function_review_complexity >= thresholds.function_extreme_complexity:
        raise ValueError("function complexity review threshold must be below the extreme threshold")

    exemptions: dict[tuple[str, str, str | None], Exemption] = {}
    exemptions_raw = raw.get("exemptions", [])
    if not isinstance(exemptions_raw, list):
        raise ValueError("exemptions must be an array of tables")
    for index, item in enumerate(exemptions_raw):
        if not isinstance(item, dict):
            raise ValueError(f"exemptions[{index}] must be a table")
        kind = item.get("kind")
        item_path = item.get("path")
        symbol = item.get("symbol")
        reason = item.get("reason")
        if kind not in {"module", "function"}:
            raise ValueError(f"exemptions[{index}].kind must be 'module' or 'function'")
        if not isinstance(item_path, str) or not item_path.strip():
            raise ValueError(f"exemptions[{index}].path must be a non-blank string")
        if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
            raise ValueError(f"exemptions[{index}].symbol must be a non-blank string or omitted")
        if kind == "module" and symbol is not None:
            raise ValueError(f"exemptions[{index}] module exemptions cannot name a symbol")
        if kind == "function" and symbol is None:
            raise ValueError(f"exemptions[{index}] function exemptions require symbol")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"exemptions[{index}].reason must be a non-blank string")
        exemption = Exemption(kind=kind, path=item_path, symbol=symbol, reason=reason)
        if exemption.key in exemptions:
            raise ValueError(f"duplicate exemption: {exemption.key!r}")
        exemptions[exemption.key] = exemption

    return Configuration(thresholds=thresholds, exemptions=exemptions)


def _exemption_reason(
    config: Configuration,
    *,
    kind: str,
    path: str,
    symbol: str | None = None,
) -> str | None:
    exemption = config.exemptions.get((kind, path, symbol))
    return exemption.reason if exemption is not None else None


def _ast_fingerprint(node: ast.AST) -> str:
    structure = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(structure.encode("utf-8")).hexdigest()


class _BranchComplexityVisitor(ast.NodeVisitor):
    """Small deterministic branch-complexity metric for one function body."""

    def __init__(self) -> None:
        self.complexity = 1

    def _visit_nested_statements(self, statements: Sequence[ast.stmt]) -> None:
        for statement in statements:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.complexity += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:  # noqa: N802
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self.complexity += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:  # noqa: N802
        self.complexity += len(node.cases)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)


def function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    visitor = _BranchComplexityVisitor()
    visitor._visit_nested_statements(node.body)
    return visitor.complexity


def _definition_start(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    decorator_lines = [decorator.lineno for decorator in node.decorator_list]
    return min([node.lineno, *decorator_lines])


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _collect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        self.functions.append((qualname, node))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._collect_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._collect_function(node)


def _function_inventory(
    tree: ast.AST,
    *,
    path: str,
    config: Configuration,
) -> list[dict[str, Any]]:
    collector = _FunctionCollector()
    collector.visit(tree)
    thresholds = config.thresholds
    result: list[dict[str, Any]] = []
    for qualname, node in collector.functions:
        end_lineno = node.end_lineno or node.lineno
        start_lineno = _definition_start(node)
        lines = end_lineno - start_lineno + 1
        complexity = function_complexity(node)
        reason = _exemption_reason(
            config,
            kind="function",
            path=path,
            symbol=qualname,
        )
        review = (
            lines > thresholds.function_review_lines
            or complexity > thresholds.function_review_complexity
        )
        extreme = (
            lines > thresholds.function_extreme_lines
            or complexity > thresholds.function_extreme_complexity
        )
        result.append(
            {
                "qualname": qualname,
                "lineno": start_lineno,
                "end_lineno": end_lineno,
                "lines": lines,
                "complexity": complexity,
                "fingerprint": _ast_fingerprint(node),
                "review": review,
                "extreme": extreme,
                "exemption": reason,
            }
        )
    return sorted(result, key=lambda item: (item["qualname"], item["lineno"]))


def build_inventory(root: Path, config: Configuration) -> dict[str, Any]:
    root = root.resolve()
    source_root = root / "src" / "ai_multi_agent_platform"
    if not source_root.is_dir():
        raise ValueError(f"production source root does not exist: {source_root}")

    thresholds = config.thresholds
    modules: list[dict[str, Any]] = []
    for file_path in sorted(source_root.rglob("*.py")):
        relative = file_path.relative_to(root).as_posix()
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        lines = len(source.splitlines())
        reason = _exemption_reason(config, kind="module", path=relative)
        functions = _function_inventory(tree, path=relative, config=config)
        modules.append(
            {
                "path": relative,
                "lines": lines,
                "fingerprint": _ast_fingerprint(tree),
                "review": lines > thresholds.module_review_lines,
                "extreme": lines > thresholds.module_extreme_lines,
                "exemption": reason,
                "functions": functions,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "thresholds": {
            "module_review_lines": thresholds.module_review_lines,
            "module_extreme_lines": thresholds.module_extreme_lines,
            "function_review_lines": thresholds.function_review_lines,
            "function_extreme_lines": thresholds.function_extreme_lines,
            "function_review_complexity": thresholds.function_review_complexity,
            "function_extreme_complexity": thresholds.function_extreme_complexity,
        },
        "modules": modules,
    }


def _all_functions(inventory: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for module in inventory["modules"]:
        for function in module["functions"]:
            yield module["path"], function


def render_markdown(inventory: Mapping[str, Any]) -> str:
    modules = list(inventory["modules"])
    functions = list(_all_functions(inventory))
    review_modules = [module for module in modules if module["review"]]
    extreme_modules = [module for module in modules if module["extreme"]]
    review_functions = [item for item in functions if item[1]["review"]]
    extreme_functions = [item for item in functions if item[1]["extreme"]]

    lines = [
        "# Maintainability inventory",
        "",
        "Line counts and branch complexity are review signals, not correctness rules.",
        "",
        f"- production modules: **{len(modules)}**",
        f"- modules above review signal: **{len(review_modules)}**",
        f"- modules above extreme signal: **{len(extreme_modules)}**",
        f"- functions/methods: **{len(functions)}**",
        f"- functions above review signal: **{len(review_functions)}**",
        f"- functions above extreme signal: **{len(extreme_functions)}**",
        "",
        "## Largest production modules",
        "",
        "| Lines | Module | Signal | Exemption |",
        "| ---: | --- | --- | --- |",
    ]
    for module in sorted(modules, key=lambda item: (-item["lines"], item["path"]))[:30]:
        signal = "extreme" if module["extreme"] else "review" if module["review"] else ""
        lines.append(
            f"| {module['lines']} | `{module['path']}` | {signal} | {module['exemption'] or ''} |"
        )

    lines.extend(
        [
            "",
            "## Largest functions/methods",
            "",
            "| Lines | Complexity | Symbol | Module | Signal | Exemption |",
            "| ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    by_size = sorted(
        functions,
        key=lambda item: (-item[1]["lines"], -item[1]["complexity"], item[0], item[1]["qualname"]),
    )
    for path, function in by_size[:40]:
        signal = "extreme" if function["extreme"] else "review" if function["review"] else ""
        lines.append(
            f"| {function['lines']} | {function['complexity']} | "
            f"`{function['qualname']}` | `{path}` | {signal} | "
            f"{function['exemption'] or ''} |"
        )

    lines.extend(
        [
            "",
            "## Highest branch complexity",
            "",
            "| Complexity | Lines | Symbol | Module | Signal | Exemption |",
            "| ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    by_complexity = sorted(
        functions,
        key=lambda item: (-item[1]["complexity"], -item[1]["lines"], item[0], item[1]["qualname"]),
    )
    for path, function in by_complexity[:40]:
        signal = "extreme" if function["extreme"] else "review" if function["review"] else ""
        lines.append(
            f"| {function['complexity']} | {function['lines']} | "
            f"`{function['qualname']}` | `{path}` | {signal} | "
            f"{function['exemption'] or ''} |"
        )
    lines.append("")
    return "\n".join(lines)


def _active_extreme(entry: Mapping[str, Any]) -> bool:
    return bool(entry["extreme"]) and entry.get("exemption") is None


def _active_extreme_fingerprint_counts(entries: Iterable[Mapping[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in entries:
        if not _active_extreme(entry):
            continue
        fingerprint = entry.get("fingerprint")
        if isinstance(fingerprint, str):
            counts[fingerprint] += 1
    return counts


def _consume_fingerprint(counts: Counter[str], fingerprint: object) -> bool:
    if not isinstance(fingerprint, str) or counts[fingerprint] <= 0:
        return False
    counts[fingerprint] -= 1
    return True


def compare_inventories(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    baseline_modules = {module["path"]: module for module in baseline["modules"]}
    baseline_module_fingerprints = _active_extreme_fingerprint_counts(baseline["modules"])
    regressions: list[str] = []

    for module in current["modules"]:
        if not _active_extreme(module):
            continue
        previous = baseline_modules.get(module["path"])
        if previous is not None and _active_extreme(previous):
            _consume_fingerprint(baseline_module_fingerprints, previous.get("fingerprint"))
            continue
        if _consume_fingerprint(baseline_module_fingerprints, module.get("fingerprint")):
            continue
        regressions.append(
            f"module {module['path']} is a newly introduced extreme ({module['lines']} lines)"
        )

    baseline_functions = {
        (path, function["qualname"]): function for path, function in _all_functions(baseline)
    }
    baseline_function_fingerprints = _active_extreme_fingerprint_counts(
        function for _, function in _all_functions(baseline)
    )
    for path, function in _all_functions(current):
        if not _active_extreme(function):
            continue
        previous = baseline_functions.get((path, function["qualname"]))
        if previous is not None and _active_extreme(previous):
            _consume_fingerprint(baseline_function_fingerprints, previous.get("fingerprint"))
            continue
        if _consume_fingerprint(baseline_function_fingerprints, function.get("fingerprint")):
            continue
        regressions.append(
            f"function {path}:{function['qualname']} is a newly introduced extreme "
            f"({function['lines']} lines, complexity {function['complexity']})"
        )
    return sorted(regressions)


def _write_inventory(
    inventory: Mapping[str, Any],
    *,
    json_out: Path,
    markdown_out: Path | None,
) -> None:
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if markdown_out is not None:
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(render_markdown(inventory), encoding="utf-8")


def _read_inventory(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported maintainability inventory: {path}")
    if not isinstance(raw.get("modules"), list):
        raise ValueError(f"maintainability inventory has no modules list: {path}")
    return raw


def _inventory_command(args: argparse.Namespace) -> int:
    configuration = load_configuration(args.config)
    inventory = build_inventory(args.root, configuration)
    _write_inventory(
        inventory,
        json_out=args.json_out,
        markdown_out=args.markdown_out,
    )
    return 0


def _compare_command(args: argparse.Namespace) -> int:
    baseline = _read_inventory(args.baseline)
    current = _read_inventory(args.current)
    regressions = compare_inventories(baseline, current)
    if regressions:
        print("New extreme maintainability outliers detected:")
        for regression in regressions:
            print(f"- {regression}")
        print(
            "Decompose the responsibility, or add a narrowly justified explicit exemption "
            "to config/maintainability.toml."
        )
        return 1
    print("No newly introduced extreme maintainability outliers.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="inventory production Python sources")
    inventory.add_argument("--root", type=Path, default=Path("."))
    inventory.add_argument(
        "--config",
        type=Path,
        default=Path("config/maintainability.toml"),
    )
    inventory.add_argument("--json-out", type=Path, required=True)
    inventory.add_argument("--markdown-out", type=Path)
    inventory.set_defaults(handler=_inventory_command)

    compare = subparsers.add_parser("compare", help="reject newly introduced extreme outliers")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--current", type=Path, required=True)
    compare.set_defaults(handler=_compare_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
