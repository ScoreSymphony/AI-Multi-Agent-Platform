"""Structured, non-secret external dependency inventory for disaster-recovery backups."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.onboarding import JsonModelProviderSetupStore


class DependencyInventoryError(RuntimeError):
    """Raised when durable dependency metadata cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class BackupExternalDependency:
    """One external runtime/recovery dependency referenced by durable platform state."""

    dependency_id: str
    kind: str
    required: bool
    restore_blocking: bool
    source: str
    recovery_action: str
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("dependency_id", "kind", "source", "recovery_action"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        copied = dict(self.metadata)
        if any(not key.strip() or not value.strip() for key, value in copied.items()):
            raise ValueError("dependency metadata must contain non-blank string keys and values")
        object.__setattr__(self, "metadata", copied)

    def to_manifest(self) -> dict[str, JsonValue]:
        return {
            "dependency_id": self.dependency_id,
            "kind": self.kind,
            "required": self.required,
            "restore_blocking": self.restore_blocking,
            "source": self.source,
            "recovery_action": self.recovery_action,
            "metadata": dict(self.metadata),
        }


def discover_single_node_external_dependencies(
    data_dir: Path,
    deployment_metadata: dict[str, object],
) -> tuple[BackupExternalDependency, ...]:
    """Discover recoverable external dependencies from durable, value-safe metadata.

    The generic backup never embeds credential values. Provider configuration contributes only
    stable provider/adapter identifiers, while SecretReference values contribute only the secret
    backend identity whose protected material must be restored separately.
    """

    dependencies: dict[str, BackupExternalDependency] = {}
    provider_store = JsonModelProviderSetupStore(data_dir / "db" / "model-providers.json")
    try:
        provider_records = provider_store.load()
    except (OSError, ValueError) as exc:
        raise DependencyInventoryError("persisted model-provider metadata is invalid") from exc

    for record in provider_records:
        _add(
            dependencies,
            BackupExternalDependency(
                dependency_id=f"model-provider:{record.provider_id}",
                kind="model-provider",
                required=True,
                restore_blocking=False,
                source="db/model-providers.json",
                recovery_action=(
                    "reinstall or reconnect the configured model-provider adapter before model "
                    "execution is required"
                ),
                metadata={
                    "provider_id": record.provider_id,
                    "adapter_id": record.adapter_id,
                },
            ),
        )
        if record.credential_ref is not None:
            _add(
                dependencies,
                BackupExternalDependency(
                    dependency_id=f"secret-provider:{record.credential_ref.provider}",
                    kind="secret-provider",
                    required=True,
                    restore_blocking=False,
                    source="db/model-providers.json:credential_ref",
                    recovery_action=(
                        "re-provision protected secret-provider key/material outside the generic "
                        "backup before the dependent provider is used"
                    ),
                    metadata={"provider": record.credential_ref.provider},
                ),
            )

    optional_adapters = deployment_metadata.get("optional_adapters", ())
    if optional_adapters is None:
        optional_adapters = ()
    if not isinstance(optional_adapters, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in optional_adapters
    ):
        raise DependencyInventoryError("optional_adapters metadata must be a list of strings")
    for adapter_id in optional_adapters:
        normalized = adapter_id.strip()
        _add(
            dependencies,
            BackupExternalDependency(
                dependency_id=f"adapter-runtime:{normalized}",
                kind="adapter-runtime",
                required=False,
                restore_blocking=False,
                source="deployment-metadata:optional_adapters",
                recovery_action="reinstall or reconnect the optional adapter when its feature is needed",
                metadata={"adapter_id": normalized},
            ),
        )

    return tuple(dependencies[key] for key in sorted(dependencies))


def _add(
    dependencies: dict[str, BackupExternalDependency],
    dependency: BackupExternalDependency,
) -> None:
    existing = dependencies.get(dependency.dependency_id)
    if existing is None:
        dependencies[dependency.dependency_id] = dependency
        return
    if existing != dependency:
        raise DependencyInventoryError(
            f"conflicting dependency metadata for {dependency.dependency_id!r}"
        )
