#!/usr/bin/env python3
"""Generate release SBOM and provenance evidence without external tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_REPOSITORY = "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform"
_SPDX_VERSION = "SPDX-2.3"
_SPDX_DATA_LICENSE = "CC0-1.0"
_SPDX_DOCUMENT_ID = "SPDXRef-DOCUMENT"
_ROOT_PACKAGE_ID = "SPDXRef-Package-ai-multi-agent-platform"


@dataclass(frozen=True, slots=True)
class _Package:
    ecosystem: str
    name: str
    version: str

    @property
    def spdx_id(self) -> str:
        value = re.sub(r"[^A-Za-z0-9.-]+", "-", f"{self.ecosystem}-{self.name}-{self.version}")
        return f"SPDXRef-Package-{value.strip('-')}"

    @property
    def purl(self) -> str | None:
        if self.version == "NOASSERTION":
            return None
        if self.ecosystem == "pypi":
            name = self.name.lower().replace("_", "-")
            return f"pkg:pypi/{name}@{self.version}"
        if self.ecosystem == "npm":
            return f"pkg:npm/{self.name}@{self.version}"
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--platform-wheel", type=Path, required=True)
    parser.add_argument("--python-declared", type=Path, required=True)
    parser.add_argument("--python-resolved", type=Path, required=True)
    parser.add_argument("--frontend-lock", type=Path, required=True)
    parser.add_argument("--sbom-output", type=Path, required=True)
    parser.add_argument("--provenance-output", type=Path, required=True)
    args = parser.parse_args()

    generate_release_supply_chain_evidence(
        source_commit=str(args.source_commit),
        created_at=str(args.created_at),
        pyproject=args.pyproject,
        platform_wheel=args.platform_wheel,
        python_declared=args.python_declared,
        python_resolved=args.python_resolved,
        frontend_lock=args.frontend_lock,
        sbom_output=args.sbom_output,
        provenance_output=args.provenance_output,
    )
    return 0


def generate_release_supply_chain_evidence(
    *,
    source_commit: str,
    created_at: str,
    pyproject: Path,
    platform_wheel: Path,
    python_declared: Path,
    python_resolved: Path,
    frontend_lock: Path,
    sbom_output: Path,
    provenance_output: Path,
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_commit) is None:
        raise ValueError("source_commit must be a full lowercase Git SHA")

    project_name, project_version = _project_identity(pyproject)
    dependencies = {
        *(_python_packages(python_resolved)),
        *(_npm_packages(frontend_lock)),
    }
    dependencies.discard(_Package("pypi", project_name, project_version))
    ordered = sorted(dependencies, key=lambda item: (item.ecosystem, item.name.lower(), item.version))

    packages = [_spdx_package(_Package("pypi", project_name, project_version))]
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
            "relatedSpdxElement": item.spdx_id,
        }
        for item in ordered
    )

    sbom = {
        "spdxVersion": _SPDX_VERSION,
        "dataLicense": _SPDX_DATA_LICENSE,
        "SPDXID": _SPDX_DOCUMENT_ID,
        "name": f"{project_name}-{project_version}",
        "documentNamespace": f"{_REPOSITORY}/spdx/{source_commit}",
        "creationInfo": {
            "created": created_at,
            "creators": ["Tool: AI-Multi-Agent-Platform release evidence generator"],
        },
        "packages": packages,
        "relationships": relationships,
    }
    _write_json(sbom_output, sbom)

    provenance = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            _subject("platform.whl", platform_wheel),
            _subject("sbom.spdx.json", sbom_output),
        ],
        "predicateType": f"{_REPOSITORY}/release-provenance/v1",
        "predicate": {
            "source": {
                "repository": _REPOSITORY,
                "commit": source_commit,
            },
            "release": {
                "name": project_name,
                "version": project_version,
                "created_at": created_at,
            },
            "builder": {
                "workflow": ".github/workflows/release-manifest.yml",
            },
            "materials": [
                _material("pyproject.toml", pyproject),
                _material("python-declared.txt", python_declared),
                _material("python-resolved.txt", python_resolved),
                _material("frontend/package-lock.json", frontend_lock),
            ],
        },
    }
    _write_json(provenance_output, provenance)


def _project_identity(path: Path) -> tuple[str, str]:
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
    return name, version


def _python_packages(path: Path) -> set[_Package]:
    packages: set[_Package] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            if name and version:
                packages.add(_Package("pypi", name.strip(), version.strip()))
            continue
        if " @ " in line:
            name, _ = line.split(" @ ", 1)
            if name:
                packages.add(_Package("pypi", name.strip(), "NOASSERTION"))
    return packages


def _npm_packages(path: Path) -> set[_Package]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("frontend lockfile must be a JSON object")
    document = cast(dict[str, object], raw)
    packages_raw = document.get("packages")
    if not isinstance(packages_raw, dict):
        raise ValueError("frontend lockfile is missing packages")

    packages: set[_Package] = set()
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
        packages.add(_Package("npm", name, version))
    return packages


def _spdx_package(package: _Package) -> dict[str, object]:
    document: dict[str, object] = {
        "SPDXID": _ROOT_PACKAGE_ID if package.name == "ai-multi-agent-platform" else package.spdx_id,
        "name": package.name,
        "versionInfo": package.version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "supplier": "NOASSERTION",
    }
    purl = package.purl
    if purl is not None:
        document["externalRefs"] = [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": purl,
            }
        ]
    return document


def _subject(name: str, path: Path) -> dict[str, object]:
    return {"name": name, "digest": {"sha256": _sha256(path)}}


def _material(name: str, path: Path) -> dict[str, object]:
    return {"uri": name, "digest": {"sha256": _sha256(path)}}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
