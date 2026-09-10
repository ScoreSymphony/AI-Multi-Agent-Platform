"""Privacy-safe storage-target identity for reference-host benchmark evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .operating_envelope import OperatingEnvelopeReport

_STORAGE_IDENTITY_VERSION = "1"
_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_MOUNTINFO_ESCAPES = {
    "\\040": " ",
    "\\011": "\t",
    "\\012": "\n",
    "\\134": "\\",
}


@dataclass(frozen=True, slots=True)
class _MountIdentity:
    filesystem_type: str
    device_id: str
    source: str
    root: str
    mount_options: tuple[str, ...]
    super_options: tuple[str, ...]

    def fingerprint(self) -> str:
        return _canonical_sha256(
            {
                "filesystem_type": self.filesystem_type,
                "device_id": self.device_id,
                "source": self.source,
                "root": self.root,
                "mount_options": list(self.mount_options),
                "super_options": list(self.super_options),
            }
        )


def attach_storage_target(
    envelope: OperatingEnvelopeReport,
    *,
    work_dir: Path,
) -> OperatingEnvelopeReport:
    """Bind an operating envelope to the filesystem actually used by the campaign."""

    environment = dict(envelope.environment)
    environment["storage_target"] = storage_target_metadata(work_dir)
    return replace(
        envelope,
        environment=environment,
        environment_fingerprint_sha256=_canonical_sha256(environment),
    )


def storage_target_metadata(work_dir: Path) -> dict[str, Any]:
    """Return stable-enough, non-secret storage identity for comparability checks."""

    resolved = work_dir.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"reference-host work directory must be a directory: {resolved}")

    total_bytes = shutil.disk_usage(resolved).total
    mount = _linux_mount_identity(resolved)
    if mount is not None:
        return {
            "identity_version": _STORAGE_IDENTITY_VERSION,
            "identity_source": "linux-mountinfo",
            "filesystem_type": mount.filesystem_type,
            "mount_fingerprint_sha256": mount.fingerprint(),
            "total_bytes": total_bytes,
        }

    stat_result = resolved.stat()
    fallback = {
        "anchor": resolved.anchor,
        "device_id": int(stat_result.st_dev),
        "total_bytes": total_bytes,
    }
    return {
        "identity_version": _STORAGE_IDENTITY_VERSION,
        "identity_source": "filesystem-stat-fallback",
        "filesystem_type": None,
        "mount_fingerprint_sha256": _canonical_sha256(fallback),
        "total_bytes": total_bytes,
    }


def _linux_mount_identity(path: Path) -> _MountIdentity | None:
    if not _MOUNTINFO_PATH.is_file():
        return None
    try:
        lines = _MOUNTINFO_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    matches: list[tuple[int, _MountIdentity]] = []
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if len(fields) <= separator + 3 or len(fields) < 6:
            continue

        mount_point = Path(_decode_mountinfo_field(fields[4]))
        if path != mount_point and mount_point not in path.parents:
            continue

        device_id = fields[2]
        root = _decode_mountinfo_field(fields[3])
        mount_options = tuple(sorted(filter(None, fields[5].split(","))))
        filesystem_type = fields[separator + 1]
        source = _decode_mountinfo_field(fields[separator + 2])
        super_options = tuple(sorted(filter(None, fields[separator + 3].split(","))))
        matches.append(
            (
                len(mount_point.parts),
                _MountIdentity(
                    filesystem_type=filesystem_type,
                    device_id=device_id,
                    source=source,
                    root=root,
                    mount_options=mount_options,
                    super_options=super_options,
                ),
            )
        )

    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _decode_mountinfo_field(value: str) -> str:
    decoded = value
    for encoded, replacement in _MOUNTINFO_ESCAPES.items():
        decoded = decoded.replace(encoded, replacement)
    return decoded


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
