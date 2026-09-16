#!/usr/bin/env python3
"""Write deterministic direct Python runtime requirements from pyproject.toml.

Unlike ``pip freeze``, this inventory is declaration-based and therefore retains
PEP 508 environment markers for dependencies that do not apply to the release
runner's operating system.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import cast


def load_declared_dependencies(pyproject_path: Path) -> tuple[str, ...]:
    """Return ``[project].dependencies`` without evaluating environment markers."""

    document = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = document.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml must contain a [project] table")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("pyproject.toml [project].dependencies must be an array")

    result: list[str] = []
    for dependency in cast(list[object], dependencies):
        if not isinstance(dependency, str) or not dependency.strip():
            raise ValueError("pyproject.toml project dependencies must be non-empty strings")
        result.append(dependency)
    return tuple(result)


def write_declared_dependencies(pyproject_path: Path, output_path: Path) -> None:
    dependencies = load_declared_dependencies(pyproject_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(f"{item}\n" for item in dependencies), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write direct Python runtime requirements without evaluating PEP 508 markers."
    )
    parser.add_argument("pyproject", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_declared_dependencies(args.pyproject, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
