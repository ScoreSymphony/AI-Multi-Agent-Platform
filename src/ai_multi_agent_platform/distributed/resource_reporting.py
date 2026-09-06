"""Canonical #14 resource-measurement availability metadata for issue #171.

``ResourceSnapshot`` keeps its scheduler-friendly numeric shape for compatibility.  The
reporting state below supplies the missing distinction between a reliable numeric zero,
a value that was not reported, and an explicitly unavailable measurement.  The state is
stored in the already durable/provider-neutral ``NodeRecord.adapter_metadata`` boundary,
so replacing a Node provider or transport does not change canonical resource identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue

RESOURCE_REPORTING_NAMESPACE = "platform.resource-reporting.v1"
RESOURCE_REPORTING_FIELDS = frozenset(
    {
        "cpu_cores_total",
        "cpu_cores_available",
        "ram_total_bytes",
        "ram_available_bytes",
        "storage_total_bytes",
        "storage_available_bytes",
        "accelerator_memory_total_bytes",
        "accelerator_memory_available_total_bytes",
    }
)


@dataclass(frozen=True, slots=True)
class ResourceReportingState:
    """Reliability state for one canonical Node resource report."""

    reported_fields: frozenset[str] = frozenset()
    unavailable_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        unknown = (self.reported_fields | self.unavailable_fields) - RESOURCE_REPORTING_FIELDS
        if unknown:
            raise ValueError(f"unknown resource reporting fields: {sorted(unknown)!r}")
        overlap = self.reported_fields & self.unavailable_fields
        if overlap:
            raise ValueError(
                f"resource fields cannot be both reported and unavailable: {sorted(overlap)!r}"
            )

    def is_reported(self, field: str, value: float | int) -> bool:
        """Return whether *value* is a reliable gauge rather than an omitted default.

        Non-zero values predate the explicit reporting marker and are necessarily supplied
        by the source, so they remain reliable for backward compatibility.  Numeric zero is
        reliable only when the source explicitly lists the field as reported.
        """

        if field not in RESOURCE_REPORTING_FIELDS:
            raise ValueError(f"unknown resource reporting field: {field}")
        if field in self.unavailable_fields:
            return False
        return field in self.reported_fields or value != 0

    def is_unavailable(self, field: str) -> bool:
        if field not in RESOURCE_REPORTING_FIELDS:
            raise ValueError(f"unknown resource reporting field: {field}")
        return field in self.unavailable_fields


def resource_reporting_metadata(
    *,
    reported_fields: tuple[str, ...] = (),
    unavailable_fields: tuple[str, ...] = (),
) -> AdapterMetadata:
    """Create canonical metadata for reliable-zero and explicitly unavailable fields."""

    state = ResourceReportingState(
        reported_fields=frozenset(reported_fields),
        unavailable_fields=frozenset(unavailable_fields),
    )
    values: dict[str, JsonValue] = {
        "reported_fields": cast(list[JsonValue], sorted(state.reported_fields)),
        "unavailable_fields": cast(list[JsonValue], sorted(state.unavailable_fields)),
    }
    return AdapterMetadata(namespace=RESOURCE_REPORTING_NAMESPACE, values=values)


def resource_reporting_state(
    metadata: tuple[AdapterMetadata, ...],
) -> ResourceReportingState:
    """Read the canonical resource-reporting namespace from Node adapter metadata.

    Malformed provider-private values are ignored rather than turning a heartbeat into a
    runtime failure.  Explicitly unavailable wins if an external adapter supplies a
    contradictory field in both sets; the conservative result never fabricates capacity.
    """

    reported: set[str] = set()
    unavailable: set[str] = set()
    for item in metadata:
        if item.namespace != RESOURCE_REPORTING_NAMESPACE:
            continue
        reported.update(_field_values(item.values.get("reported_fields")))
        unavailable.update(_field_values(item.values.get("unavailable_fields")))
    reported.difference_update(unavailable)
    return ResourceReportingState(
        reported_fields=frozenset(reported),
        unavailable_fields=frozenset(unavailable),
    )


def _field_values(value: JsonValue | None) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item in RESOURCE_REPORTING_FIELDS}


__all__ = [
    "RESOURCE_REPORTING_FIELDS",
    "RESOURCE_REPORTING_NAMESPACE",
    "ResourceReportingState",
    "resource_reporting_metadata",
    "resource_reporting_state",
]
