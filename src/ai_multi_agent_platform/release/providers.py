"""Optional provider-neutral Git remote discovery adapter for issue #42."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .discovery import (
    UPDATE_OBSERVATION_SCHEMA_VERSION,
    CompatibilityInventory,
    ObservedUpstream,
    UpdateClassification,
    UpdateDiscoveryError,
)

_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


@dataclass(frozen=True, slots=True)
class GitDiscoveryResult:
    observed_at: str
    observations: tuple[ObservedUpstream, ...]
    errors: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": UPDATE_OBSERVATION_SCHEMA_VERSION,
            "observed_at": self.observed_at,
            "components": [
                {
                    "component": item.component,
                    "source_url": item.source_url,
                    "revision": item.revision,
                    "license": item.license,
                    "classifications": [value.value for value in item.classifications],
                    "release_ref": item.release_ref,
                    "published_at": item.published_at,
                    "patch_conflicts": list(item.patch_conflicts),
                    "validation": {},
                }
                for item in self.observations
            ],
            "provider_errors": dict(self.errors),
        }


def git_head_revision(source_url: str) -> str:
    """Resolve a repository HEAD without cloning or mutating a working tree."""

    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--exit-code", source_url, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateDiscoveryError(f"git discovery failed for {source_url}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git exited with {completed.returncode}"
        raise UpdateDiscoveryError(f"git discovery failed for {source_url}: {detail}")
    line = completed.stdout.strip().splitlines()
    if len(line) != 1:
        raise UpdateDiscoveryError(f"git discovery returned an unexpected HEAD for {source_url}")
    revision = line[0].split(maxsplit=1)[0].lower()
    if _GIT_COMMIT.fullmatch(revision) is None:
        raise UpdateDiscoveryError(f"git discovery returned a non-immutable HEAD for {source_url}")
    return revision


def discover_git_heads(
    inventory: CompatibilityInventory,
    *,
    observed_at: str,
    resolver: Callable[[str], str] = git_head_revision,
) -> GitDiscoveryResult:
    """Discover immutable remote HEADs as advisory observations.

    This adapter deliberately does not infer release semantics, licenses, or compatibility.
    A changed revision is classified UNKNOWN so adoption remains behind manual review and gates.
    """

    observations: list[ObservedUpstream] = []
    errors: dict[str, str] = {}
    for entry in inventory.entries:
        try:
            remote_revision = resolver(entry.source_url).lower()
            if _GIT_COMMIT.fullmatch(remote_revision) is None:
                raise UpdateDiscoveryError(
                    f"Git resolver returned a non-immutable HEAD for {entry.source_url}"
                )
        except UpdateDiscoveryError as exc:
            errors[entry.component] = str(exc)
            continue
        revision = (
            entry.revision
            if _revision_contains(entry.revision, remote_revision)
            else remote_revision
        )
        classifications = () if revision == entry.revision else (UpdateClassification.UNKNOWN,)
        observations.append(
            ObservedUpstream(
                component=entry.component,
                source_url=entry.source_url,
                revision=revision,
                license=entry.license,
                classifications=classifications,
                release_ref=f"git:{entry.source_url}@{remote_revision}",
            )
        )
    return GitDiscoveryResult(
        observed_at=observed_at,
        observations=tuple(observations),
        errors=errors,
    )


def write_git_discovery_result(result: GitDiscoveryResult, path: str | Path) -> None:
    destination = Path(path)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        raise UpdateDiscoveryError(f"cannot write upstream observation snapshot: {exc}") from exc


def _revision_contains(current_revision: str, remote_revision: str) -> bool:
    return current_revision == remote_revision or remote_revision in current_revision
