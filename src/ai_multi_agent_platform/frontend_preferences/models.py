"""Canonical personal frontend-presentation preference model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.types import JsonValue

FRONTEND_CUSTOMIZATION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FrontendPreference:
    """One authenticated principal's presentation-only frontend preference."""

    principal_ref: str
    customization: dict[str, JsonValue] | None = None
    schema_version: int = FRONTEND_CUSTOMIZATION_SCHEMA_VERSION
    revision: int = 0
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.principal_ref.strip():
            raise ValueError("principal_ref must not be blank")
        if self.schema_version != FRONTEND_CUSTOMIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported frontend customization schema version")
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
        if self.updated_at is not None and self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.customization is not None:
            object.__setattr__(self, "customization", dict(self.customization))

    @classmethod
    def empty(cls, principal_ref: str) -> "FrontendPreference":
        return cls(principal_ref=principal_ref)

    def next_revision(
        self,
        customization: dict[str, JsonValue],
        *,
        now: datetime | None = None,
    ) -> "FrontendPreference":
        current = now or datetime.now(UTC)
        return FrontendPreference(
            principal_ref=self.principal_ref,
            customization=dict(customization),
            revision=self.revision + 1,
            updated_at=current,
        )
