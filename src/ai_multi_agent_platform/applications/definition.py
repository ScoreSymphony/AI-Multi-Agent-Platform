"""Canonical installed Application definition independent of runtime-private identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import ApplicationManifest, utc_now


@dataclass(frozen=True, slots=True)
class Application:
    """Installed canonical Application definition selected for a runtime adapter.

    ``runtime_id`` names the platform runtime implementation chosen for this installed
    definition. Provider-private container, process, project or stack identifiers are
    deliberately excluded from this model and remain backend implementation details.
    """

    manifest: ApplicationManifest
    runtime_id: str
    source_ref: str | None = None
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    installed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.runtime_id.strip():
            raise ValueError("runtime_id must not be blank")
        if self.source_ref is not None and not self.source_ref.strip():
            raise ValueError("source_ref must not be blank when provided")
        if self.installed_at.tzinfo is None or self.installed_at.utcoffset() is None:
            raise ValueError("installed_at must be timezone-aware")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    @property
    def application_id(self) -> str:
        return self.manifest.application_id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def name(self) -> str:
        return self.manifest.name
