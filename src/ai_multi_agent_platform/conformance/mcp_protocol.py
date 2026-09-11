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
MCP_COMPATIBILITY_EVIDENCE_SCHEMA = "ai-multi-agent-platform/mcp-compatibility/v2"
MCP_PIN_SCHEMA = "ai-multi-agent-platform/mcp-conformance-pins/v1"


class MCPProtocolScenarioStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNSUPPORTED = "unsupported"


class MCPProtocolEvidenceStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


class MCPPlatformEvidenceStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


class MCPProfileClaimStatus(StrEnum):
    CLAIMED = "claimed"
    NOT_CLAIMED = "not_claimed"
    UNSUPPORTED = "unsupported"


class MCPCompatibilityResult(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    INCOMPLETE = "incomplete"
    NOT_CLAIMED = "not_claimed"
    UNSUPPORTED = "unsupported"


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
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None


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
class MCPProfileIdentity:
    protocol_revision: str
    mode: str
    transport_profile: str


@dataclass(frozen=True, slots=True)
class MCPPlatformProfileEvidence:
    identity: MCPProfileIdentity
    deployment_profile: str
    platform_commit: str | None
    platform_release: str | None
    platform_conformant: bool
    claimed: bool


@dataclass(frozen=True, slots=True)
class MCPCompatibilityProfileEvidence:
    identity: MCPProfileIdentity
    platform_deployment_profile: str | None
    platform_commit: str | None
    platform_release: str | None
    claim_status: str
    result: str
    reason: str | None
    protocol_evidence_status: str
    protocol_conformant: bool | None
    platform_evidence_status: str
    platform_conformant: bool | None
    suite_version: str | None
    suite_commit: str | None
    adapter_revision: str | None
    sdk_version: str | None


@dataclass(frozen=True, slots=True)
class MCPCompatibilityEvidence:
    schema: str
    profiles: tuple[MCPCompatibilityProfileEvidence, ...]

    @property
    def compatible(self) -> bool:
        """Whether every claimed profile has both matching evidence dimensions and passes."""

        claimed = [
            profile
            for profile in self.profiles
            if profile.claim_status == MCPProfileClaimStatus.CLAIMED.value
        ]
        return bool(claimed) and all(
            profile.result == MCPCompatibilityResult.COMPATIBLE.value for profile in claimed
        )

    def to_json(self) -> str:
        # Deliberately no ambiguous top-level ``compatible`` field: compatibility is per profile.
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    def validate(self) -> None:
        identities = [profile.identity for profile in self.profiles]
        if len(set(identities)) != len(identities):
            raise ValueError("MCP compatibility matrix contains duplicate profile identities")


def combine_mcp_compatibility(
    protocol: MCPProtocolEvidence,
    *,
    platform: MCPPlatformProfileEvidence,
    additional_profiles: tuple[MCPCompatibilityProfileEvidence, ...] = (),
) -> MCPCompatibilityEvidence:
    """Combine only evidence for the exact same revision/direction/transport profile."""

    protocol.validate()
    protocol_identity = MCPProfileIdentity(
        protocol_revision=protocol.protocol_revision,
        mode=protocol.mode,
        transport_profile=protocol.transport_profile,
    )
    profiles: list[MCPCompatibilityProfileEvidence] = []

    if protocol_identity == platform.identity:
        claim_status = (
            MCPProfileClaimStatus.CLAIMED
            if protocol.claimed and platform.claimed
            else MCPProfileClaimStatus.NOT_CLAIMED
        )
        if claim_status is MCPProfileClaimStatus.NOT_CLAIMED:
            result = MCPCompatibilityResult.NOT_CLAIMED
        elif protocol.protocol_conformant and platform.platform_conformant:
            result = MCPCompatibilityResult.COMPATIBLE
        else:
            result = MCPCompatibilityResult.INCOMPATIBLE
        profiles.append(
            _profile_with_both_evidence(
                protocol,
                platform,
                claim_status=claim_status,
                result=result,
            )
        )
    else:
        mismatch_reason = (
            "protocol and platform evidence refer to different MCP profiles; evidence cannot be "
            "combined across revision, direction, or transport boundaries"
        )
        profiles.append(
            _protocol_only_profile(
                protocol,
                result=(
                    MCPCompatibilityResult.INCOMPLETE
                    if protocol.claimed
                    else MCPCompatibilityResult.NOT_CLAIMED
                ),
                reason=mismatch_reason,
            )
        )
        profiles.append(
            _platform_only_profile(
                platform,
                result=(
                    MCPCompatibilityResult.INCOMPLETE
                    if platform.claimed
                    else MCPCompatibilityResult.NOT_CLAIMED
                ),
                reason=mismatch_reason,
            )
        )

    evidence = MCPCompatibilityEvidence(
        schema=MCP_COMPATIBILITY_EVIDENCE_SCHEMA,
        profiles=tuple(profiles) + additional_profiles,
    )
    evidence.validate()
    return evidence


def missing_mcp_protocol_evidence(
    *,
    platform: MCPPlatformProfileEvidence,
    additional_profiles: tuple[MCPCompatibilityProfileEvidence, ...] = (),
) -> MCPCompatibilityEvidence:
    """Represent a #46 MCP platform result with no official proof for that exact profile."""

    evidence = MCPCompatibilityEvidence(
        schema=MCP_COMPATIBILITY_EVIDENCE_SCHEMA,
        profiles=(
            _platform_only_profile(
                platform,
                result=(
                    MCPCompatibilityResult.INCOMPLETE
                    if platform.claimed
                    else MCPCompatibilityResult.NOT_CLAIMED
                ),
                reason="official MCP protocol evidence is missing for this exact profile",
            ),
        )
        + additional_profiles,
    )
    evidence.validate()
    return evidence


def unsupported_mcp_profile(
    *,
    protocol_revision: str,
    mode: str,
    transport_profile: str,
    reason: str,
) -> MCPCompatibilityProfileEvidence:
    return MCPCompatibilityProfileEvidence(
        identity=MCPProfileIdentity(protocol_revision, mode, transport_profile),
        platform_deployment_profile=None,
        platform_commit=None,
        platform_release=None,
        claim_status=MCPProfileClaimStatus.UNSUPPORTED.value,
        result=MCPCompatibilityResult.UNSUPPORTED.value,
        reason=reason,
        protocol_evidence_status=MCPProtocolEvidenceStatus.UNSUPPORTED.value,
        protocol_conformant=None,
        platform_evidence_status=MCPPlatformEvidenceStatus.UNSUPPORTED.value,
        platform_conformant=None,
        suite_version=None,
        suite_commit=None,
        adapter_revision=None,
        sdk_version=None,
    )


def not_claimed_mcp_profile(
    *,
    protocol_revision: str,
    mode: str,
    transport_profile: str,
    reason: str,
) -> MCPCompatibilityProfileEvidence:
    return MCPCompatibilityProfileEvidence(
        identity=MCPProfileIdentity(protocol_revision, mode, transport_profile),
        platform_deployment_profile=None,
        platform_commit=None,
        platform_release=None,
        claim_status=MCPProfileClaimStatus.NOT_CLAIMED.value,
        result=MCPCompatibilityResult.NOT_CLAIMED.value,
        reason=reason,
        protocol_evidence_status=MCPProtocolEvidenceStatus.MISSING.value,
        protocol_conformant=None,
        platform_evidence_status=MCPPlatformEvidenceStatus.MISSING.value,
        platform_conformant=None,
        suite_version=None,
        suite_commit=None,
        adapter_revision=None,
        sdk_version=None,
    )


def _profile_with_both_evidence(
    protocol: MCPProtocolEvidence,
    platform: MCPPlatformProfileEvidence,
    *,
    claim_status: MCPProfileClaimStatus,
    result: MCPCompatibilityResult,
) -> MCPCompatibilityProfileEvidence:
    return MCPCompatibilityProfileEvidence(
        identity=platform.identity,
        platform_deployment_profile=platform.deployment_profile,
        platform_commit=platform.platform_commit,
        platform_release=platform.platform_release,
        claim_status=claim_status.value,
        result=result.value,
        reason=None,
        protocol_evidence_status=MCPProtocolEvidenceStatus.PRESENT.value,
        protocol_conformant=protocol.protocol_conformant,
        platform_evidence_status=MCPPlatformEvidenceStatus.PRESENT.value,
        platform_conformant=platform.platform_conformant,
        suite_version=protocol.suite_version,
        suite_commit=protocol.suite_commit,
        adapter_revision=protocol.adapter_revision,
        sdk_version=protocol.sdk_version,
    )


def _protocol_only_profile(
    protocol: MCPProtocolEvidence,
    *,
    result: MCPCompatibilityResult,
    reason: str,
) -> MCPCompatibilityProfileEvidence:
    return MCPCompatibilityProfileEvidence(
        identity=MCPProfileIdentity(
            protocol.protocol_revision,
            protocol.mode,
            protocol.transport_profile,
        ),
        platform_deployment_profile=None,
        platform_commit=None,
        platform_release=None,
        claim_status=(
            MCPProfileClaimStatus.CLAIMED.value
            if protocol.claimed
            else MCPProfileClaimStatus.NOT_CLAIMED.value
        ),
        result=result.value,
        reason=reason,
        protocol_evidence_status=MCPProtocolEvidenceStatus.PRESENT.value,
        protocol_conformant=protocol.protocol_conformant,
        platform_evidence_status=MCPPlatformEvidenceStatus.MISSING.value,
        platform_conformant=None,
        suite_version=protocol.suite_version,
        suite_commit=protocol.suite_commit,
        adapter_revision=protocol.adapter_revision,
        sdk_version=protocol.sdk_version,
    )


def _platform_only_profile(
    platform: MCPPlatformProfileEvidence,
    *,
    result: MCPCompatibilityResult,
    reason: str,
) -> MCPCompatibilityProfileEvidence:
    return MCPCompatibilityProfileEvidence(
        identity=platform.identity,
        platform_deployment_profile=platform.deployment_profile,
        platform_commit=platform.platform_commit,
        platform_release=platform.platform_release,
        claim_status=(
            MCPProfileClaimStatus.CLAIMED.value
            if platform.claimed
            else MCPProfileClaimStatus.NOT_CLAIMED.value
        ),
        result=result.value,
        reason=reason,
        protocol_evidence_status=MCPProtocolEvidenceStatus.MISSING.value,
        protocol_conformant=None,
        platform_evidence_status=MCPPlatformEvidenceStatus.PRESENT.value,
        platform_conformant=platform.platform_conformant,
        suite_version=None,
        suite_commit=None,
        adapter_revision=None,
        sdk_version=None,
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
        stdout_artifact=_optional_string(value.get("stdout_artifact")),
        stderr_artifact=_optional_string(value.get("stderr_artifact")),
    )


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _required_string(value, "optional string")
