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
        return {
            "platform_release": versions.platform_release,
            "versions": running_vector,
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
