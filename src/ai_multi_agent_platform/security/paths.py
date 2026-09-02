"""Filesystem confinement helpers for workspace-owned operations."""

from __future__ import annotations

from pathlib import Path


class PathSecurityError(ValueError):
    """Raised when an untrusted path violates a configured filesystem boundary."""


def resolve_within(
    root: str | Path,
    relative_path: str | Path,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve an untrusted relative path while confining it to ``root``.

    The helper rejects absolute paths, explicit parent traversal, NUL bytes and
    symlink-resolved escapes. It intentionally performs no filesystem writes.
    Callers remain responsible for avoiding time-of-check/time-of-use races in
    hostile multi-process environments; production executors should add OS-level
    sandboxing where required.
    """

    root_path = Path(root).resolve(strict=True)
    candidate = Path(relative_path)

    raw = str(relative_path)
    if "\x00" in raw:
        raise PathSecurityError("path contains a NUL byte")
    if candidate.is_absolute():
        raise PathSecurityError("absolute paths are not allowed")
    if any(part == ".." for part in candidate.parts):
        raise PathSecurityError("parent traversal is not allowed")

    resolved = (root_path / candidate).resolve(strict=must_exist)
    if resolved != root_path and root_path not in resolved.parents:
        raise PathSecurityError("path escapes configured root")
    return resolved
