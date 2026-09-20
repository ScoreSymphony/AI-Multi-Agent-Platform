"""Fail-closed projection of the repository release version onto canonical release surfaces."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_SEMVER = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_RUNTIME_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)

_PYPROJECT = Path("pyproject.toml")
_RUNTIME_INIT = Path("src/ai_multi_agent_platform/__init__.py")
_REPOSITORY_COMPATIBILITY = Path("release/compatibility.json")
_PACKAGED_COMPATIBILITY = Path("src/ai_multi_agent_platform/release/compatibility.json")


class ReleaseVersionProjectionError(ValueError):
    """Raised when release-version projection cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class ReleaseVersionProjectionReport:
    current_version: str
    target_version: str
    changed_files: tuple[str, ...]
    written: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "target_version": self.target_version,
            "changed_files": list(self.changed_files),
            "written": self.written,
        }


@dataclass(frozen=True, slots=True)
class _ProjectionState:
    version: str
    contents: dict[Path, str]


def project_release_version(
    root: str | Path,
    *,
    target_version: str,
    write: bool = False,
) -> ReleaseVersionProjectionReport:
    """Project one SemVer onto every canonical repository release-version surface.

    The command refuses to repair an already inconsistent source tree. This prevents a release
    bump from hiding pre-existing drift between package metadata, runtime metadata and the
    repository/packaged compatibility inventories.
    """

    root_path = Path(root).resolve()
    _validate_version(target_version)
    state = _read_projection_state(root_path)

    rendered = _render_projection(state, target_version=target_version)
    changed = tuple(
        str(path.as_posix())
        for path in (_PYPROJECT, _RUNTIME_INIT, _REPOSITORY_COMPATIBILITY, _PACKAGED_COMPATIBILITY)
        if rendered[path] != state.contents[path]
    )

    if write and changed:
        _write_projection(root_path, state=state, rendered=rendered)
        verified = _read_projection_state(root_path)
        if verified.version != target_version:
            raise ReleaseVersionProjectionError(
                "release-version projection verification failed after writing"
            )

    return ReleaseVersionProjectionReport(
        current_version=state.version,
        target_version=target_version,
        changed_files=changed,
        written=bool(write and changed),
    )


def _read_projection_state(root: Path) -> _ProjectionState:
    paths = (_PYPROJECT, _RUNTIME_INIT, _REPOSITORY_COMPATIBILITY, _PACKAGED_COMPATIBILITY)
    contents: dict[Path, str] = {}
    for relative in paths:
        path = root / relative
        try:
            contents[relative] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReleaseVersionProjectionError(
                f"cannot read release-version surface {relative}: {exc}"
            ) from exc

    pyproject_version = _pyproject_version(contents[_PYPROJECT])
    runtime_version = _runtime_version(contents[_RUNTIME_INIT])
    repository_document = _compatibility_document(
        contents[_REPOSITORY_COMPATIBILITY],
        label=str(_REPOSITORY_COMPATIBILITY),
    )
    packaged_document = _compatibility_document(
        contents[_PACKAGED_COMPATIBILITY],
        label=str(_PACKAGED_COMPATIBILITY),
    )
    if repository_document != packaged_document:
        raise ReleaseVersionProjectionError(
            "repository and packaged compatibility inventories differ before version projection"
        )

    repository_release = _compatibility_version(
        repository_document,
        label=str(_REPOSITORY_COMPATIBILITY),
    )
    packaged_release = _compatibility_version(
        packaged_document,
        label=str(_PACKAGED_COMPATIBILITY),
    )
    observed = {
        "pyproject.toml": pyproject_version,
        "runtime __version__": runtime_version,
        "repository compatibility": repository_release,
        "packaged compatibility": packaged_release,
    }
    if len(set(observed.values())) != 1:
        detail = ", ".join(f"{name}={version}" for name, version in observed.items())
        raise ReleaseVersionProjectionError(
            f"release-version surfaces are inconsistent before projection: {detail}"
        )
    return _ProjectionState(version=pyproject_version, contents=contents)


def _render_projection(state: _ProjectionState, *, target_version: str) -> dict[Path, str]:
    rendered = dict(state.contents)
    rendered[_PYPROJECT] = _replace_assignment(
        state.contents[_PYPROJECT],
        name="version",
        current=state.version,
        target=target_version,
        label=str(_PYPROJECT),
    )
    rendered[_RUNTIME_INIT] = _replace_assignment(
        state.contents[_RUNTIME_INIT],
        name="__version__",
        current=state.version,
        target=target_version,
        label=str(_RUNTIME_INIT),
    )

    for relative in (_REPOSITORY_COMPATIBILITY, _PACKAGED_COMPATIBILITY):
        document = _compatibility_document(state.contents[relative], label=str(relative))
        document["platform_release"] = target_version
        versions = cast(dict[str, object], document["versions"])
        versions["platform_release"] = target_version
        rendered[relative] = json.dumps(document, indent=2) + "\n"
    return rendered


def _write_projection(
    root: Path,
    *,
    state: _ProjectionState,
    rendered: dict[Path, str],
) -> None:
    prepared: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    try:
        for relative, content in rendered.items():
            if content == state.contents[relative]:
                continue
            destination = root / relative
            temporary = destination.with_name(f".{destination.name}.release-version.tmp")
            temporary.write_text(content, encoding="utf-8")
            prepared.append((destination, temporary))

        for destination, temporary in prepared:
            temporary.replace(destination)
            replaced.append(destination)
    except OSError as exc:
        for _, temporary in prepared:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
        for destination in replaced:
            relative = destination.relative_to(root)
            try:
                destination.write_text(state.contents[relative], encoding="utf-8")
            except OSError:
                pass
        raise ReleaseVersionProjectionError(
            f"cannot write release-version projection safely: {exc}"
        ) from exc


def _pyproject_version(content: str) -> str:
    try:
        document = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseVersionProjectionError(f"pyproject.toml is invalid TOML: {exc}") from exc
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseVersionProjectionError("pyproject.toml is missing [project]")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseVersionProjectionError("pyproject.toml project.version is missing")
    return version


def _runtime_version(content: str) -> str:
    matches = list(_RUNTIME_VERSION.finditer(content))
    if len(matches) != 1:
        raise ReleaseVersionProjectionError(
            "runtime package must contain exactly one __version__ assignment"
        )
    return matches[0].group(1)


def _compatibility_document(content: str, *, label: str) -> dict[str, object]:
    try:
        raw: object = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReleaseVersionProjectionError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReleaseVersionProjectionError(f"{label} must be a JSON object")
    document = cast(dict[str, object], raw)
    versions = document.get("versions")
    if not isinstance(versions, dict):
        raise ReleaseVersionProjectionError(f"{label} is missing versions object")
    return document


def _compatibility_version(document: dict[str, object], *, label: str) -> str:
    release = document.get("platform_release")
    versions = cast(dict[str, object], document["versions"])
    nested = versions.get("platform_release")
    if not isinstance(release, str) or not isinstance(nested, str):
        raise ReleaseVersionProjectionError(f"{label} is missing platform_release")
    if release != nested:
        raise ReleaseVersionProjectionError(
            f"{label} platform_release does not match versions.platform_release"
        )
    return release


def _replace_assignment(
    content: str,
    *,
    name: str,
    current: str,
    target: str,
    label: str,
) -> str:
    pattern = re.compile(
        rf'^{re.escape(name)} = "{re.escape(current)}"$',
        re.MULTILINE,
    )
    updated, count = pattern.subn(f'{name} = "{target}"', content)
    if count != 1:
        raise ReleaseVersionProjectionError(
            f"{label} must contain exactly one canonical {name} assignment"
        )
    return updated


def _validate_version(value: str) -> None:
    if _SEMVER.fullmatch(value) is None:
        raise ReleaseVersionProjectionError(
            "target release version must be a SemVer value such as 1.0.0 or 1.0.0-rc.1"
        )
