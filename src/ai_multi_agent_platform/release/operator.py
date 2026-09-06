"""Read-only operator status for releases and upstream synchronization."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.upgrade.versioning import current_release_versions

from .discovery import (
    CompatibilityInventory,
    UpdateDiscoveryReport,
    UpdateDisposition,
    evaluate_update_candidates,
    load_compatibility_inventory,
)
from .models import ReleaseManifest
from .service import release_metadata


@dataclass(slots=True)
class ReleaseOperatorService:
    """Project release/update state without owning deployment or upgrade activation."""

    inventory: CompatibilityInventory
    manifest: ReleaseManifest | None = None
    discovery: UpdateDiscoveryReport | None = None

    @classmethod
    def packaged_defaults(cls) -> ReleaseOperatorService:
        return cls(inventory=load_compatibility_inventory())

    def status(self) -> dict[str, object]:
        versions = current_release_versions()
        discovery = self.discovery or evaluate_update_candidates(
            self.inventory,
            enabled=False,
        )
        manifest_metadata = None if self.manifest is None else release_metadata(self.manifest)
        warnings: list[str] = []
        if self.inventory.platform_release != versions.platform_release:
            warnings.append(
                "compatibility inventory platform_release does not match the running platform release"
            )
        return {
            "platform_release": versions.platform_release,
            "versions": versions.to_dict(),
            "release_manifest": manifest_metadata,
            "compatibility_inventory": self.inventory.to_dict(),
            "update_discovery": discovery.to_dict(),
            "release_ready": None
            if manifest_metadata is None
            else bool(manifest_metadata["release_ready"]),
            "operator_warnings": warnings,
            "automatic_production_updates": False,
            "production_pin_mutation": "not_permitted_by_discovery",
        }

    def set_discovery_report(self, report: UpdateDiscoveryReport) -> None:
        if report.mode not in {
            UpdateDisposition.CURRENT,
            UpdateDisposition.DISABLED,
            UpdateDisposition.OFFLINE,
        }:
            raise ValueError(f"invalid discovery report mode: {report.mode.value}")
        self.discovery = report
