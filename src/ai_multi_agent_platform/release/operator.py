"""Read-only operator status for releases and upstream synchronization."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.upgrade.versioning import current_release_versions

from .discovery import (
    CompatibilityInventory,
    UpdateDiscoveryError,
    UpdateDiscoveryReport,
    UpdateDisposition,
    evaluate_update_candidates,
    load_compatibility_inventory,
)
from .models import ReleaseManifest
from .persistence import JsonDiscoveryReportStore, StoredDiscoveryReport
from .service import release_metadata


@dataclass(slots=True)
class ReleaseOperatorService:
    """Project release/update state without owning deployment or upgrade activation."""

    inventory: CompatibilityInventory
    manifest: ReleaseManifest | None = None
    discovery: UpdateDiscoveryReport | None = None
    discovery_reviewed_at: str | None = None
    discovery_store: JsonDiscoveryReportStore | None = None
    discovery_load_error: str | None = None

    @classmethod
    def packaged_defaults(cls) -> ReleaseOperatorService:
        return cls(inventory=load_compatibility_inventory())

    @classmethod
    def runtime_defaults(cls) -> ReleaseOperatorService:
        """Load the latest advisory report from the standard runtime data root when available."""

        data_dir = Path(os.environ.get("AI_MAP_DATA_DIR", ".data/single-node")).expanduser()
        return cls.for_data_dir(data_dir)

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> ReleaseOperatorService:
        store = JsonDiscoveryReportStore.for_data_dir(data_dir)
        try:
            stored = store.read()
        except UpdateDiscoveryError as exc:
            return cls(
                inventory=load_compatibility_inventory(),
                discovery_store=store,
                discovery_load_error=str(exc),
            )
        if stored is None:
            return cls(inventory=load_compatibility_inventory(), discovery_store=store)
        return cls(
            inventory=load_compatibility_inventory(),
            discovery=stored.report,
            discovery_reviewed_at=stored.reviewed_at,
            discovery_store=store,
        )

    def status(self) -> dict[str, object]:
        versions = current_release_versions()
        discovery = self.discovery or evaluate_update_candidates(self.inventory, enabled=False)
        manifest_metadata = None if self.manifest is None else release_metadata(self.manifest)
        warnings: list[str] = []
        running_vector = versions.to_dict()
        reviewed_vector = self.inventory.versions.to_dict()
        if reviewed_vector != running_vector:
            mismatched = sorted(
                name for name in running_vector if running_vector[name] != reviewed_vector.get(name)
            )
            warnings.append(
                "compatibility inventory version vector does not match the running platform: "
                + ", ".join(mismatched)
            )
        if self.discovery_load_error is not None:
            warnings.append(
                "persisted upstream discovery could not be loaded; using disabled advisory state: "
                + self.discovery_load_error
            )
        return {
            "platform_release": versions.platform_release,
            "versions": running_vector,
            "release_manifest": manifest_metadata,
            "compatibility_inventory": self.inventory.to_dict(),
            "update_discovery": discovery.to_dict(),
            "update_discovery_reviewed_at": self.discovery_reviewed_at,
            "release_ready": None
            if manifest_metadata is None
            else bool(manifest_metadata["release_ready"]),
            "operator_warnings": warnings,
            "automatic_production_updates": False,
            "production_pin_mutation": "not_permitted_by_discovery",
        }

    def set_discovery_report(
        self,
        report: UpdateDiscoveryReport,
        *,
        reviewed_at: str | None = None,
        persist: bool = False,
    ) -> None:
        if report.mode not in {
            UpdateDisposition.CURRENT,
            UpdateDisposition.DISABLED,
            UpdateDisposition.OFFLINE,
        }:
            raise ValueError(f"invalid discovery report mode: {report.mode.value}")
        if persist:
            if self.discovery_store is None:
                raise ValueError("discovery persistence is not configured")
            if reviewed_at is None:
                raise ValueError("reviewed_at is required when persisting discovery")
            self.discovery_store.write(
                StoredDiscoveryReport(reviewed_at=reviewed_at, report=report)
            )
        self.discovery = report
        self.discovery_reviewed_at = reviewed_at
        self.discovery_load_error = None
