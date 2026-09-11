"""Typed evidence for official MCP wire-protocol conformance.

This module is deliberately conformance infrastructure, not a canonical runtime contract.
MCP remains an optional adapter behind platform-owned capability interfaces.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

MCP_PROTOCOL_EVIDENCE_SCHEMA = "ai-multi-agent-platform/mcp-protocol-conformance/v1"
MCP_COMPATIBILITY_EVIDENCE_SCHEMA = "ai-multi-agent-platform/mcp-compatibility/v1"
MCP_PIN_SCHEMA = "ai-multi-agent-platform/mcp-conformance-pins/v1"


class MCPProtocolScenarioStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNSUPPORTED = "unsupported"


class MCPProtocolEvidenceStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class MCPConformanceTrack:
    name: str
    suite_version: str
    suite_commit: str
    protocol_revision: str
    mode: str
    transport_profile: str
    claimed: bool
    gating: bool
    scenarios: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCPConformancePins:
    upstream_repository: str
    tracks: tuple[MCPConformanceTrack, ...]

    def track(self, name: str) -> MCPConformanceTrack:
        for track in self.tracks:
            if track.name == name:
                return track
        raise KeyError(f"unknown MCP conformance track: {name}")


@dataclass(frozen=True, slots=True)
class MCPProtocolScenarioEvidence:
    scenario_id: str
    status: str
    exit_code: int
    diagnostics: str | None
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True, slots=True)
class MCPProtocolEvidence:
    schema: str
    platform_commit: str | None
    platform_release: str | None
    adapter_revision: str
    sdk_version: str | None
    protocol_revision: str
    suite_repository: str
    suite_version: str
    suite_commit: str
    mode: str
    transport_profile: str
    claimed: bool
    gating: bool
    protocol_conformant: bool
    timestamp_utc: str
    environment: dict[str, str]
    scenarios: tuple[MCPProtocolScenarioEvidence, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> MCPProtocolEvidence:
        raw = json.loads(text)
        if not isinstance(raw, Mapping):
            raise ValueError("MCP protocol evidence must be a JSON object")
        if raw.get("schema") != MCP_PROTOCOL_EVIDENCE_SCHEMA:
            raise ValueError("unsupported MCP protocol evidence schema")

        scenarios_raw = raw.get("scenarios")
        if not isinstance(scenarios_raw, list):
            raise ValueError("MCP protocol evidence scenarios must be a JSON array")
        scenarios = tuple(_scenario_from_mapping(item) for item in scenarios_raw)
        if not scenarios:
            raise ValueError("MCP protocol evidence requires at least one scenario")

        environment_raw = raw.get("environment")
        if not isinstance(environment_raw, Mapping):
            raise ValueError("MCP protocol evidence environment must be a JSON object")
        environment = {
            _required_string(key, "environment key"): _required_string(value, "environment value")
            for key, value in environment_raw.items()
        }

        protocol_conformant = raw.get("protocol_conformant")
        claimed = raw.get("claimed")
        gating = raw.get("gating")
        if not isinstance(protocol_conformant, bool):
            raise ValueError("protocol_conformant must be boolean")
        if not isinstance(claimed, bool) or not isinstance(gating, bool):
            raise ValueError("claimed and gating must be boolean")

        evidence = cls(
            schema=MCP_PROTOCOL_EVIDENCE_SCHEMA,
            platform_commit=_optional_string(raw.get("platform_commit")),
            platform_release=_optional_string(raw.get("platform_release")),
            adapter_revision=_required_string(raw.get("adapter_revision"), "adapter_revision"),
            sdk_version=_optional_string(raw.get("sdk_version")),
            protocol_revision=_required_string(raw.get("protocol_revision"), "protocol_revision"),
            suite_repository=_required_string(raw.get("suite_repository"), "suite_repository"),
            suite_version=_required_string(raw.get("suite_version"), "suite_version"),
            suite_commit=_required_string(raw.get("suite_commit"), "suite_commit"),
            mode=_required_string(raw.get("mode"), "mode"),
            transport_profile=_required_string(raw.get("transport_profile"), "transport_profile"),
            claimed=claimed,
            gating=gating,
            protocol_conformant=protocol_conformant,
            timestamp_utc=_required_string(raw.get("timestamp_utc"), "timestamp_utc"),
            environment=environment,
            scenarios=scenarios,
        )
        evidence.validate()
        return evidence

    def validate(self) -> None:
        statuses = {scenario.status for scenario in self.scenarios}
        known = {status.value for status in MCPProtocolScenarioStatus}
        if not statuses <= known:
            unknown = sorted(statuses - known)
            raise ValueError(f"unknown MCP scenario status: {', '.join(unknown)}")
        expected = all(
            scenario.status == MCPProtocolScenarioStatus.PASS.value for scenario in self.scenarios
        )
        if self.protocol_conformant != expected:
            raise ValueError("protocol_conformant does not match scenario results")
        if self.gating and not self.claimed:
            raise ValueError("an unclaimed MCP conformance track cannot be gating")


@dataclass(frozen=True, slots=True)
class MCPCompatibilityEvidence:
    schema: str
    protocol_evidence_status: str
    protocol_revision: str | None
    suite_version: str | None
    suite_commit: str | None
    adapter_revision: str | None
    sdk_version: str | None
    protocol_conformant: bool | None
    platform_conformant: bool
    claimed: bool

    @property
    def compatible(self) -> bool:
        return self.claimed and self.protocol_conformant is True and self.platform_conformant

    def to_json(self) -> str:
        payload = asdict(self)
        payload["compatible"] = self.compatible
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def combine_mcp_compatibility(
    protocol: MCPProtocolEvidence,
    *,
    platform_conformant: bool,
) -> MCPCompatibilityEvidence:
    """Keep official protocol evidence distinct from #46 platform integration evidence."""

    protocol.validate()
    return MCPCompatibilityEvidence(
        schema=MCP_COMPATIBILITY_EVIDENCE_SCHEMA,
        protocol_evidence_status=MCPProtocolEvidenceStatus.PRESENT.value,
        protocol_revision=protocol.protocol_revision,
        suite_version=protocol.suite_version,
        suite_commit=protocol.suite_commit,
        adapter_revision=protocol.adapter_revision,
        sdk_version=protocol.sdk_version,
        protocol_conformant=protocol.protocol_conformant,
        platform_conformant=platform_conformant,
        claimed=protocol.claimed,
    )


def missing_mcp_protocol_evidence(*, platform_conformant: bool) -> MCPCompatibilityEvidence:
    """Represent a #46 MCP platform result that has no official protocol proof attached."""

    return MCPCompatibilityEvidence(
        schema=MCP_COMPATIBILITY_EVIDENCE_SCHEMA,
        protocol_evidence_status=MCPProtocolEvidenceStatus.MISSING.value,
        protocol_revision=None,
        suite_version=None,
        suite_commit=None,
        adapter_revision=None,
        sdk_version=None,
        protocol_conformant=None,
        platform_conformant=platform_conformant,
        claimed=False,
    )


def load_mcp_conformance_pins(path: Path) -> MCPConformancePins:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("MCP conformance pin manifest must be a JSON object")
    if raw.get("schema") != MCP_PIN_SCHEMA:
        raise ValueError("unsupported MCP conformance pin manifest schema")
    repository = _required_string(raw.get("upstream_repository"), "upstream_repository")
    tracks_raw = raw.get("tracks")
    if not isinstance(tracks_raw, Mapping) or not tracks_raw:
        raise ValueError("MCP conformance pin manifest requires tracks")

    tracks: list[MCPConformanceTrack] = []
    for name, value in tracks_raw.items():
        track_name = _required_string(name, "track name")
        if not isinstance(value, Mapping):
            raise ValueError(f"MCP conformance track {track_name!r} must be an object")
        scenarios_raw = value.get("scenarios")
        if not isinstance(scenarios_raw, list) or not scenarios_raw:
            raise ValueError(f"MCP conformance track {track_name!r} requires scenarios")
        scenarios = tuple(
            _required_string(scenario, f"{track_name}.scenarios") for scenario in scenarios_raw
        )
        claimed = value.get("claimed")
        gating = value.get("gating")
        if not isinstance(claimed, bool) or not isinstance(gating, bool):
            raise ValueError(f"MCP conformance track {track_name!r} requires boolean flags")
        if gating and not claimed:
            raise ValueError(
                f"MCP conformance track {track_name!r} cannot gate an unclaimed profile"
            )
        tracks.append(
            MCPConformanceTrack(
                name=track_name,
                suite_version=_required_string(value.get("suite_version"), "suite_version"),
                suite_commit=_required_string(value.get("suite_commit"), "suite_commit"),
                protocol_revision=_required_string(
                    value.get("protocol_revision"), "protocol_revision"
                ),
                mode=_required_string(value.get("mode"), "mode"),
                transport_profile=_required_string(
                    value.get("transport_profile"), "transport_profile"
                ),
                claimed=claimed,
                gating=gating,
                scenarios=scenarios,
            )
        )
    return MCPConformancePins(upstream_repository=repository, tracks=tuple(tracks))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scenario_from_mapping(value: object) -> MCPProtocolScenarioEvidence:
    if not isinstance(value, Mapping):
        raise ValueError("MCP scenario evidence must be an object")
    exit_code = value.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("MCP scenario evidence exit_code must be integer")
    return MCPProtocolScenarioEvidence(
        scenario_id=_required_string(value.get("scenario_id"), "scenario_id"),
        status=_required_string(value.get("status"), "status"),
        exit_code=exit_code,
        diagnostics=_optional_string(value.get("diagnostics")),
        stdout_sha256=_required_string(value.get("stdout_sha256"), "stdout_sha256"),
        stderr_sha256=_required_string(value.get("stderr_sha256"), "stderr_sha256"),
    )


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _required_string(value, "optional string")
