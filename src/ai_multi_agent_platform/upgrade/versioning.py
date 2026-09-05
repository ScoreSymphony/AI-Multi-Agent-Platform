"""Independent platform version dimensions and durable upgrade state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import BACKUP_FORMAT_VERSION
from ai_multi_agent_platform.control_plane import API_VERSION
from ai_multi_agent_platform.distributed import WORKER_PROTOCOL_VERSION
from ai_multi_agent_platform.domain import SCHEMA_VERSION
from ai_multi_agent_platform.messaging import ENVELOPE_VERSION
from ai_multi_agent_platform.plugins.models import PLUGIN_MANIFEST_VERSION
from ai_multi_agent_platform.portability import (
    PORTABLE_FORMAT_VERSION,
    TEMPLATE_PORTABLE_SCHEMA_VERSION,
)

from .models import VersionSnapshot

VERSION_STATE_SCHEMA = "1"
BASELINE_MIGRATION_REVISION = "baseline"


class VersionStateError(RuntimeError):
    """Raised when durable upgrade/version metadata is absent or invalid."""


def current_release_versions(
    *,
    migration_revision: str = BASELINE_MIGRATION_REVISION,
    adapter_versions: Mapping[str, str] | None = None,
    plugin_interface_versions: Mapping[str, str] | None = None,
) -> VersionSnapshot:
    """Return the release's independent compatibility/version dimensions.

    Adapter and plugin-interface versions are deployment-specific and therefore supplied by
    the composition that owns those installed extensions instead of being invented by core.
    """

    return VersionSnapshot(
        platform_release=__version__,
        domain_schema=SCHEMA_VERSION,
        api=API_VERSION,
        migration_revision=migration_revision,
        plugin_manifest=PLUGIN_MANIFEST_VERSION,
        portable_format=PORTABLE_FORMAT_VERSION,
        template_schema=TEMPLATE_PORTABLE_SCHEMA_VERSION,
        backup_format=str(BACKUP_FORMAT_VERSION),
        worker_protocol=WORKER_PROTOCOL_VERSION,
        message_protocol=ENVELOPE_VERSION,
        adapter_versions=adapter_versions or {},
        plugin_interface_versions=plugin_interface_versions or {},
    )


class JsonVersionStateStore:
    """Atomic deployment-local store for the last successfully activated version vector."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> JsonVersionStateStore:
        return cls(Path(data_dir) / "db" / "platform-upgrade.json")

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> VersionSnapshot:
        if not self.path.is_file():
            raise VersionStateError(
                "upgrade version state is not initialized; run the explicit baseline adoption step"
            )
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VersionStateError(f"cannot read upgrade version state: {exc}") from exc
        if not isinstance(raw, dict):
            raise VersionStateError("upgrade version state must be a JSON object")
        if raw.get("state_schema") != VERSION_STATE_SCHEMA:
            raise VersionStateError("unsupported upgrade version-state schema")
        versions = raw.get("versions")
        if not isinstance(versions, dict):
            raise VersionStateError("upgrade version state is missing versions")
        return version_snapshot_from_dict(versions)

    def initialize(self, snapshot: VersionSnapshot | None = None) -> VersionSnapshot:
        if self.path.exists():
            raise VersionStateError("upgrade version state is already initialized")
        adopted = snapshot or current_release_versions()
        self.write(adopted)
        return adopted

    def write(self, snapshot: VersionSnapshot) -> None:
        document = {
            "state_schema": VERSION_STATE_SCHEMA,
            "versions": snapshot.to_dict(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def version_snapshot_from_dict(value: Mapping[object, object]) -> VersionSnapshot:
    """Decode one persisted version vector using the same validation as the state store."""

    def required(name: str) -> str:
        raw = value.get(name)
        if not isinstance(raw, str) or not raw:
            raise VersionStateError(f"version state field {name!r} must be a non-empty string")
        return raw

    def string_map(name: str) -> dict[str, str]:
        raw = value.get(name, {})
        if not isinstance(raw, dict):
            raise VersionStateError(f"version state field {name!r} must be an object")
        result: dict[str, str] = {}
        for key, item in raw.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise VersionStateError(f"version state field {name!r} must contain strings")
            result[key] = item
        return result

    return VersionSnapshot(
        platform_release=required("platform_release"),
        domain_schema=required("domain_schema"),
        api=required("api"),
        migration_revision=required("migration_revision"),
        plugin_manifest=required("plugin_manifest"),
        portable_format=required("portable_format"),
        template_schema=required("template_schema"),
        backup_format=required("backup_format"),
        worker_protocol=required("worker_protocol"),
        message_protocol=required("message_protocol"),
        adapter_versions=string_map("adapter_versions"),
        plugin_interface_versions=string_map("plugin_interface_versions"),
    )
