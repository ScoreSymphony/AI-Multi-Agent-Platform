from __future__ import annotations

import argparse
import ast
import json
import tomllib
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "ai_multi_agent_platform"
PLATFORM_PACKAGE = "ai_multi_agent_platform"


def top_level_packages() -> tuple[str, ...]:
    return tuple(
        sorted(
            path.name
            for path in SOURCE_ROOT.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()
        )
    )


def _package_parts(path: Path) -> tuple[str, ...]:
    return path.relative_to(SOURCE_ROOT).with_suffix("").parts[:-1]


def _import_candidates(path: Path, tree: ast.AST) -> Iterable[str]:
    package_parts = _package_parts(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            parent_count = node.level - 1
            if parent_count > len(package_parts):
                continue
            base_parts = package_parts[: len(package_parts) - parent_count]
            if node.module:
                base_parts = (*base_parts, *node.module.split("."))
            base = ".".join((PLATFORM_PACKAGE, *base_parts))
        else:
            base = node.module or ""

        if not base:
            continue
        yield base
        yield from (f"{base}.{alias.name}" for alias in node.names if alias.name != "*")


def _target_top_level(candidate: str, packages: set[str]) -> str | None:
    prefix = f"{PLATFORM_PACKAGE}."
    if not candidate.startswith(prefix):
        return None
    remainder = candidate[len(prefix) :]
    target = remainder.split(".", 1)[0]
    return target if target in packages else None


def dependency_graph() -> dict[str, tuple[str, ...]]:
    package_names = top_level_packages()
    packages = set(package_names)
    graph: dict[str, set[str]] = {name: set() for name in package_names}

    for source in package_names:
        for path in (SOURCE_ROOT / source).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for candidate in _import_candidates(path, tree):
                target = _target_top_level(candidate, packages)
                if target is not None and target != source:
                    graph[source].add(target)

    return {name: tuple(sorted(targets)) for name, targets in graph.items()}


def strongly_connected_components(
    graph: dict[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in graph[node]:
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] != indices[node]:
            return

        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        components.append(tuple(sorted(component)))

    for node in graph:
        if node not in indices:
            visit(node)

    return tuple(sorted(components))


def cyclic_components(graph: dict[str, tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        component for component in strongly_connected_components(graph) if len(component) > 1
    )


def _baseline(path: Path) -> tuple[bool, tuple[frozenset[str], ...]]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    enforce = data.get("enforce", True)
    if not isinstance(enforce, bool):
        raise ValueError("dependency-cycle baseline enforce must be a boolean")

    entries = data.get("cycle", [])
    if not isinstance(entries, list):
        raise ValueError("dependency-cycle baseline must define [[cycle]] entries")

    cycles: list[frozenset[str]] = []
    for entry in entries:
        packages = entry.get("packages")
        if (
            not isinstance(packages, list)
            or not packages
            or not all(isinstance(package, str) and package for package in packages)
        ):
            raise ValueError("each dependency-cycle baseline entry needs non-empty packages")
        cycles.append(frozenset(packages))
    return enforce, tuple(cycles)


def _unexpected_cycles(
    cycles: tuple[tuple[str, ...], ...],
    baseline: tuple[frozenset[str], ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        cycle
        for cycle in cycles
        if not any(frozenset(cycle).issubset(allowed) for allowed in baseline)
    )


def _summary(graph: dict[str, tuple[str, ...]]) -> dict[str, object]:
    cycles = cyclic_components(graph)
    return {
        "package_count": len(graph),
        "edge_count": sum(len(targets) for targets in graph.values()),
        "cyclic_component_count": len(cycles),
        "cyclic_components": [list(component) for component in cycles],
        "dependencies": {name: list(targets) for name, targets in graph.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit top-level ai_multi_agent_platform package dependencies and cycles."
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    graph = dependency_graph()
    summary = _summary(graph)

    if args.as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"package_count={summary['package_count']}")
        print(f"edge_count={summary['edge_count']}")
        print(f"cyclic_component_count={summary['cyclic_component_count']}")
        for component in cyclic_components(graph):
            print("cycle=" + ",".join(component))

    if args.baseline is None:
        return 0

    enforce, baseline = _baseline(args.baseline)
    if not enforce:
        print("cycle_baseline_enforcement=disabled")
        return 0

    unexpected = _unexpected_cycles(cyclic_components(graph), baseline)
    if not unexpected:
        return 0

    print("unexpected_dependency_cycles:")
    for component in unexpected:
        print("  - " + ", ".join(component))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
