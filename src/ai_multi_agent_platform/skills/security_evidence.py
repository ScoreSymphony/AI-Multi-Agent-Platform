"""Provider-neutral immutable pre-install security evidence for canonical Skills.

Security evidence is advisory input to the #588 trust lifecycle. It deliberately has
no authority to trust, approve, install, enable, reject, delete or activate a Skill.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


class SecurityEvidenceStatus(StrEnum):
    CLEAN = "clean"
    FINDINGS = "findings"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """One provider finding, preserving stable rule and per-run occurrence IDs separately."""

    rule_id: str | None
    occurrence_id: str | None
    category: str | None = None
    severity: str | None = None
    confidence: float | None = None
    summary: str | None = None
    path: str | None = None
    line: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.rule_id, "security finding rule_id"),
            (self.occurrence_id, "security finding occurrence_id"),
            (self.category, "security finding category"),
            (self.severity, "security finding severity"),
            (self.summary, "security finding summary"),
            (self.path, "security finding path"),
        ):
            if value is not None:
                _require_nonblank(value, name)
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("security finding confidence must be between 0 and 1")
        if self.line is not None and self.line < 1:
            raise ValueError("security finding line must be >= 1")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class SecurityEvidence:
    """Immutable evidence for exactly one external Skill revision and content digest."""

    evidence_id: str
    provider: str
    provider_version: str
    provider_revision: str
    provider_build_identity: str
    dependency_set_digest: str
    scan_mode: str
    policy_config_revision: str
    observed_at: datetime
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    status: SecurityEvidenceStatus
    complete: bool
    findings: tuple[SecurityFinding, ...] = ()
    degraded_reasons: tuple[str, ...] = ()
    suppression_metadata: tuple[Mapping[str, JsonValue], ...] = ()
    baseline_metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    network_usage: Mapping[str, JsonValue] = field(default_factory=dict)
    provider_usage: Mapping[str, JsonValue] = field(default_factory=dict)
    provider_metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    known_provider_limitations: tuple[str, ...] = ()
    raw_report_digest: str | None = None
    raw_report_artifact_ref: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.evidence_id, "security_evidence")
        validate_id(self.candidate_id, "skill")
        if self.candidate_revision < 1:
            raise ValueError("candidate_revision must be >= 1")
        _validate_digest(self.candidate_digest, "candidate digest")
        for value, name in (
            (self.provider, "security evidence provider"),
            (self.provider_version, "security evidence provider version"),
            (self.provider_revision, "security evidence provider revision"),
            (self.provider_build_identity, "security evidence provider build identity"),
            (self.scan_mode, "security evidence scan mode"),
            (self.policy_config_revision, "security evidence policy/config revision"),
        ):
            _require_nonblank(value, name)
        _validate_digest(self.dependency_set_digest, "dependency-set digest")
        if self.raw_report_digest is not None:
            _validate_digest(self.raw_report_digest, "raw-report digest")
        if self.raw_report_artifact_ref is not None:
            _require_nonblank(self.raw_report_artifact_ref, "raw-report artifact reference")
        if self.complete and self.status is SecurityEvidenceStatus.DEGRADED:
            raise ValueError("degraded security evidence cannot be complete")
        if not self.complete and self.status is not SecurityEvidenceStatus.DEGRADED:
            raise ValueError("incomplete security evidence must be degraded")
        if self.status is SecurityEvidenceStatus.CLEAN and self.findings:
            raise ValueError("clean security evidence cannot contain findings")
        if self.status is SecurityEvidenceStatus.FINDINGS and not self.findings:
            raise ValueError("findings status requires at least one finding")
        if self.status is SecurityEvidenceStatus.DEGRADED and not self.degraded_reasons:
            raise ValueError("degraded security evidence requires at least one degradation reason")
        if len(set(self.degraded_reasons)) != len(self.degraded_reasons):
            raise ValueError("security evidence degradation reasons must be unique")
        if len(set(self.known_provider_limitations)) != len(self.known_provider_limitations):
            raise ValueError("known provider limitations must be unique")
        for value in (*self.degraded_reasons, *self.known_provider_limitations):
            _require_nonblank(value, "security evidence reason/limitation")
        object.__setattr__(
            self,
            "suppression_metadata",
            tuple(MappingProxyType(dict(item)) for item in self.suppression_metadata),
        )
        object.__setattr__(self, "baseline_metadata", _freeze_mapping(self.baseline_metadata))
        object.__setattr__(self, "network_usage", _freeze_mapping(self.network_usage))
        object.__setattr__(self, "provider_usage", _freeze_mapping(self.provider_usage))
        object.__setattr__(self, "provider_metadata", _freeze_mapping(self.provider_metadata))


@dataclass(frozen=True, slots=True)
class StagedSkillCandidate:
    """Exact platform-staged local snapshot supplied to an evidence provider.

    The provider contract accepts only a local directory. Source acquisition (Git, URL,
    archives, marketplace resolution) belongs to canonical Skill intake and provenance.
    """

    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    snapshot_path: Path

    def __post_init__(self) -> None:
        validate_id(self.candidate_id, "skill")
        if self.candidate_revision < 1:
            raise ValueError("candidate_revision must be >= 1")
        _validate_digest(self.candidate_digest, "candidate digest")
        if not isinstance(self.snapshot_path, Path):
            raise TypeError("snapshot_path must be a pathlib.Path")


@dataclass(frozen=True, slots=True)
class RawReportArtifact:
    digest: str
    artifact_ref: str
    size_bytes: int

    def __post_init__(self) -> None:
        _validate_digest(self.digest, "raw-report artifact digest")
        _require_nonblank(self.artifact_ref, "raw-report artifact reference")
        if self.size_bytes < 0:
            raise ValueError("raw-report artifact size must be >= 0")


class RawSecurityReportStore(Protocol):
    def put(self, payload: bytes) -> RawReportArtifact: ...


class FilesystemRawSecurityReportStore:
    """Content-addressed immutable raw-report store for self-hosted baseline deployments."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, payload: bytes) -> RawReportArtifact:
        digest = sha256(payload).hexdigest()
        target = self.root / f"{digest}.json"
        if not target.exists():
            fd, temp_name = tempfile.mkstemp(prefix=f".{digest}.", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_name, 0o600)
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return RawReportArtifact(
            digest=digest,
            artifact_ref=f"security-evidence://raw/{digest}",
            size_bytes=len(payload),
        )


class SecurityEvidenceProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    def scan(self, candidate: StagedSkillCandidate) -> SecurityEvidence: ...


class SecurityEvidenceRepository(Protocol):
    def add(self, evidence: SecurityEvidence) -> None: ...

    def get(self, evidence_id: str) -> SecurityEvidence: ...

    def list_for_candidate(
        self,
        candidate_id: str,
        candidate_revision: int | None = None,
    ) -> tuple[SecurityEvidence, ...]: ...

    def list_all(self) -> tuple[SecurityEvidence, ...]: ...


class InMemorySecurityEvidenceRepository:
    def __init__(self) -> None:
        self._evidence: dict[str, SecurityEvidence] = {}

    def add(self, evidence: SecurityEvidence) -> None:
        existing = self._evidence.get(evidence.evidence_id)
        if existing is not None:
            if existing != evidence:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "security evidence identity is immutable",
                )
            return
        self._evidence[evidence.evidence_id] = evidence

    def get(self, evidence_id: str) -> SecurityEvidence:
        try:
            return self._evidence[evidence_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"security evidence not found: {evidence_id}",
            ) from exc

    def list_for_candidate(
        self,
        candidate_id: str,
        candidate_revision: int | None = None,
    ) -> tuple[SecurityEvidence, ...]:
        values = [item for item in self._evidence.values() if item.candidate_id == candidate_id]
        if candidate_revision is not None:
            values = [item for item in values if item.candidate_revision == candidate_revision]
        return tuple(sorted(values, key=lambda item: (item.observed_at, item.evidence_id)))

    def list_all(self) -> tuple[SecurityEvidence, ...]:
        return tuple(
            sorted(self._evidence.values(), key=lambda item: (item.observed_at, item.evidence_id))
        )


class JsonSecurityEvidenceRepository(InMemorySecurityEvidenceRepository):
    """Durable immutable evidence repository with an atomic JSON snapshot."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__()
        self._load()

    def add(self, evidence: SecurityEvidence) -> None:
        before = len(self._evidence)
        super().add(evidence)
        if len(self._evidence) != before:
            self._persist()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != self.SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence store"
            )
        values = payload.get("evidence")
        if not isinstance(values, list):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence list"
            )
        for item in values:
            if not isinstance(item, dict):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence item"
                )
            super().add(security_evidence_from_json(item))

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "evidence": [security_evidence_to_json(item) for item in self.list_all()],
        }
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class SecurityEvidenceService:
    """Platform-owned evidence orchestration without Skill trust-state mutation authority."""

    def __init__(
        self,
        repository: SecurityEvidenceRepository,
        providers: tuple[SecurityEvidenceProvider, ...] = (),
    ) -> None:
        self.repository = repository
        self._providers: dict[str, SecurityEvidenceProvider] = {}
        for provider in providers:
            self.register_provider(provider)

    def register_provider(self, provider: SecurityEvidenceProvider) -> None:
        provider_id = provider.provider_id
        _require_nonblank(provider_id, "security evidence provider ID")
        if provider_id in self._providers:
            raise ContractError(
                ErrorCode.CONFLICT, f"security evidence provider exists: {provider_id}"
            )
        self._providers[provider_id] = provider

    def unregister_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def scan(self, provider_id: str, candidate: StagedSkillCandidate) -> SecurityEvidence:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"security evidence provider not found: {provider_id}"
            )
        if not provider.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE, f"security evidence provider disabled: {provider_id}"
            )
        evidence = provider.scan(candidate)
        self._validate_binding(provider_id, candidate, evidence)
        self.repository.add(evidence)
        return evidence

    def _validate_binding(
        self,
        provider_id: str,
        candidate: StagedSkillCandidate,
        evidence: SecurityEvidence,
    ) -> None:
        if evidence.provider != provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "security evidence provider identity mismatch",
            )
        if (
            evidence.candidate_id != candidate.candidate_id
            or evidence.candidate_revision != candidate.candidate_revision
            or evidence.candidate_digest != candidate.candidate_digest
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "security evidence is not bound to the requested candidate revision/digest",
            )


def new_security_evidence_id() -> str:
    return new_id("security_evidence")


def security_evidence_to_json(evidence: SecurityEvidence) -> dict[str, JsonValue]:
    return {
        "evidence_id": evidence.evidence_id,
        "provider": evidence.provider,
        "provider_version": evidence.provider_version,
        "provider_revision": evidence.provider_revision,
        "provider_build_identity": evidence.provider_build_identity,
        "dependency_set_digest": evidence.dependency_set_digest,
        "scan_mode": evidence.scan_mode,
        "policy_config_revision": evidence.policy_config_revision,
        "observed_at": evidence.observed_at.isoformat(),
        "candidate_id": evidence.candidate_id,
        "candidate_revision": evidence.candidate_revision,
        "candidate_digest": evidence.candidate_digest,
        "status": evidence.status.value,
        "complete": evidence.complete,
        "findings": [
            {
                "rule_id": item.rule_id,
                "occurrence_id": item.occurrence_id,
                "category": item.category,
                "severity": item.severity,
                "confidence": item.confidence,
                "summary": item.summary,
                "path": item.path,
                "line": item.line,
                "metadata": dict(item.metadata),
            }
            for item in evidence.findings
        ],
        "degraded_reasons": list(evidence.degraded_reasons),
        "suppression_metadata": [dict(item) for item in evidence.suppression_metadata],
        "baseline_metadata": dict(evidence.baseline_metadata),
        "network_usage": dict(evidence.network_usage),
        "provider_usage": dict(evidence.provider_usage),
        "provider_metadata": dict(evidence.provider_metadata),
        "known_provider_limitations": list(evidence.known_provider_limitations),
        "raw_report_digest": evidence.raw_report_digest,
        "raw_report_artifact_ref": evidence.raw_report_artifact_ref,
    }


def security_evidence_from_json(payload: Mapping[str, object]) -> SecurityEvidence:
    findings_payload = payload.get("findings", [])
    if not isinstance(findings_payload, list):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence findings"
        )
    findings: list[SecurityFinding] = []
    for item in findings_payload:
        if not isinstance(item, Mapping):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence finding"
            )
        metadata = item.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        findings.append(
            SecurityFinding(
                rule_id=_optional_str(item.get("rule_id")),
                occurrence_id=_optional_str(item.get("occurrence_id")),
                category=_optional_str(item.get("category")),
                severity=_optional_str(item.get("severity")),
                confidence=_optional_float(item.get("confidence")),
                summary=_optional_str(item.get("summary")),
                path=_optional_str(item.get("path")),
                line=_optional_int(item.get("line")),
                metadata=_mapping_json(metadata),
            )
        )
    suppressions = payload.get("suppression_metadata", [])
    if not isinstance(suppressions, list):
        suppressions = []
    suppression_values = tuple(
        _mapping_json(item) for item in suppressions if isinstance(item, Mapping)
    )
    return SecurityEvidence(
        evidence_id=str(payload["evidence_id"]),
        provider=str(payload["provider"]),
        provider_version=str(payload["provider_version"]),
        provider_revision=str(payload["provider_revision"]),
        provider_build_identity=str(payload["provider_build_identity"]),
        dependency_set_digest=str(payload["dependency_set_digest"]),
        scan_mode=str(payload["scan_mode"]),
        policy_config_revision=str(payload["policy_config_revision"]),
        observed_at=datetime.fromisoformat(str(payload["observed_at"])),
        candidate_id=str(payload["candidate_id"]),
        candidate_revision=int(payload["candidate_revision"]),
        candidate_digest=str(payload["candidate_digest"]),
        status=SecurityEvidenceStatus(str(payload["status"])),
        complete=bool(payload["complete"]),
        findings=tuple(findings),
        degraded_reasons=_string_tuple(payload.get("degraded_reasons")),
        suppression_metadata=suppression_values,
        baseline_metadata=_mapping_json(payload.get("baseline_metadata")),
        network_usage=_mapping_json(payload.get("network_usage")),
        provider_usage=_mapping_json(payload.get("provider_usage")),
        provider_metadata=_mapping_json(payload.get("provider_metadata")),
        known_provider_limitations=_string_tuple(payload.get("known_provider_limitations")),
        raw_report_digest=_optional_str(payload.get("raw_report_digest")),
        raw_report_artifact_ref=_optional_str(payload.get("raw_report_artifact_ref")),
    )


def _mapping_json(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise ContractError(
        ErrorCode.INVALID_PROVIDER_RESPONSE,
        f"value is not JSON-compatible: {type(value).__name__}",
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
