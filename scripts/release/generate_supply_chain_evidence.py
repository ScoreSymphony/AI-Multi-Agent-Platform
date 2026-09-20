#!/usr/bin/env python3
"""Generate a deterministic SPDX release SBOM without external SBOM tooling."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from urllib.parse import quote

_REPOSITORY = "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform"
_SPDX_DOCUMENT_ID = "SPDXRef-DOCUMENT"
_ROOT_PACKAGE_ID = "SPDXRef-Package-ai-multi-agent-platform"

type Package = tuple[str, str, str]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--python-declared", type=Path, required=True)
    parser.add_argument("--python-resolved", type=Path, required=True)
    parser.add_argument("--frontend-lock", type=Path, required=True)
    parser.add_argument("--sbom-output", type=Path, required=True)
    args = parser.parse_args()

    generate_release_sbom(
        source_commit=str(args.source_commit),
        created_at=str(args.created_at),
        pyproject=args.pyproject,
        python_declared=args.python_declared,
        python_resolved=args.python_resolved,
        frontend_lock=args.frontend_lock,
        sbom_output=args.sbom_output,
    )
    return 0


def generate_release_sbom(
    *,
    source_commit: str,
    created_at: str,
    pyproject: Path,
    python_declared: Path,
    python_resolved: Path,
    frontend_lock: Path,
    sbom_output: Path,
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_commit) is None:
        raise ValueError("source_commit must be a full lowercase Git SHA")

    project_name, project_version, project_license = _project_identity(pyproject)
    dependencies = {
        *_python_packages(python_declared),
        *_python_packages(python_resolved),
        *_npm_packages(frontend_lock),
    }
    dependencies = {
        item
        for item in dependencies
        if not (
            item[0] == "pypi"
            and _normalize_python_name(item[1]) == _normalize_python_name(project_name)
        )
    }
    ordered = sorted(dependencies, key=lambda item: (item[0], item[1].lower(), item[2]))

    packages = [
        _spdx_package(
            ("pypi", project_name, project_version),
            root=True,
            license_declared=project_license,
        )
    ]
    packages.extend(_spdx_package(item) for item in ordered)
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": _SPDX_DOCUMENT_ID,
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": _ROOT_PACKAGE_ID,
        }
    ]
    relationships.extend(
        {
            "spdxElementId": _ROOT_PACKAGE_ID,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": _spdx_id(item),
        }
        for item in ordered
    )

    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": _SPDX_DOCUMENT_ID,
        "name": f"{project_name}-{project_version}",
        "documentNamespace": f"{_REPOSITORY}/spdx/{source_commit}",
        "creationInfo": {
            "created": _spdx_timestamp(created_at),
            "creators": ["Tool: AI-Multi-Agent-Platform release evidence generator"],
        },
        "packages": packages,
        "relationships": relationships,
    }
    sbom_output.parent.mkdir(parents=True, exist_ok=True)
    sbom_output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _spdx_timestamp(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("created_at must include an explicit timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_identity(path: Path) -> tuple[str, str, str]:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    project = document.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject is missing [project]")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError("pyproject project.name is missing")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject project.version is missing")
    license_value = project.get("license")
    license_text = "NOASSERTION"
    if isinstance(license_value, dict):
        candidate = license_value.get("text")
        if isinstance(candidate, str) and candidate:
            license_text = candidate
    return name, version, license_text


def _python_packages(path: Path) -> set[Package]:
    packages: set[Package] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            version = version.split(";", maxsplit=1)[0].strip()
            if name and version:
                packages.add(("pypi", name.strip(), version))
            continue
        if " @ " in line:
            name, _ = line.split(" @ ", 1)
            if name:
                packages.add(("pypi", name.strip(), "NOASSERTION"))
    return packages


def _npm_packages(path: Path) -> set[Package]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("frontend lockfile must be a JSON object")
    document = cast(dict[str, object], raw)
    packages_raw = document.get("packages")
    if not isinstance(packages_raw, dict):
        raise ValueError("frontend lockfile is missing packages")

    packages: set[Package] = set()
    for location, metadata_raw in packages_raw.items():
        if not isinstance(location, str) or not location or "node_modules/" not in location:
            continue
        if not isinstance(metadata_raw, dict):
            continue
        metadata = cast(dict[str, object], metadata_raw)
        version = metadata.get("version")
        if not isinstance(version, str) or not version:
            continue
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            name = location.rsplit("node_modules/", maxsplit=1)[-1]
        packages.add(("npm", name, version))
    return packages


def _normalize_python_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _spdx_id(package: Package) -> str:
    ecosystem, name, version = package
    value = re.sub(r"[^A-Za-z0-9.-]+", "-", f"{ecosystem}-{name}-{version}")
    return f"SPDXRef-Package-{value.strip('-')}"


def _purl(package: Package) -> str | None:
    ecosystem, name, version = package
    if version == "NOASSERTION":
        return None
    if ecosystem == "pypi":
        return f"pkg:pypi/{_normalize_python_name(name)}@{version}"
    if ecosystem == "npm":
        encoded_name = quote(name, safe="/")
        return f"pkg:npm/{encoded_name}@{version}"
    return None


def _spdx_package(
    package: Package,
    *,
    root: bool = False,
    license_declared: str = "NOASSERTION",
) -> dict[str, object]:
    _, name, version = package
    document: dict[str, object] = {
        "SPDXID": _ROOT_PACKAGE_ID if root else _spdx_id(package),
        "name": name,
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": license_declared,
        "supplier": "NOASSERTION",
    }
    purl = _purl(package)
    if purl is not None:
        document["externalRefs"] = [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": purl,
            }
        ]
    return document


if __name__ == "__main__":
    raise SystemExit(main())
