"""Immutable advisory evidence for pre-install Skill review."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


def _digest(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        frozen = MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
        return cast(JsonValue, frozen)
    if isinstance(value, list):
        return cast(JsonValue, tuple(_freeze_value(item) for item in value))
    return value


def _freeze(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


class SecurityEvidenceStatus(StrEnum):
    CLEAN = "clean"
    FINDINGS = "findings"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class SecurityFinding:
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
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.line is not None and self.line < 1:
            raise ValueError("line must be >= 1")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class SecurityEvidence:
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
        _digest(self.candidate_digest, "candidate digest")
        _digest(self.dependency_set_digest, "dependency-set digest")
        for value, name in (
            (self.provider, "provider"),
            (self.provider_version, "provider version"),
            (self.provider_revision, "provider revision"),
            (self.provider_build_identity, "provider build identity"),
            (self.scan_mode, "scan mode"),
            (self.policy_config_revision, "policy/config revision"),
        ):
            _text(value, name)
        if self.raw_report_digest is not None:
            _digest(self.raw_report_digest, "raw-report digest")
        if self.complete == (self.status is SecurityEvidenceStatus.DEGRADED):
            raise ValueError("complete/degraded state is inconsistent")
        if self.status is SecurityEvidenceStatus.CLEAN and self.findings:
            raise ValueError("clean evidence cannot contain findings")
        if self.status is SecurityEvidenceStatus.FINDINGS and not self.findings:
            raise ValueError("findings state requires findings")
        if self.status is SecurityEvidenceStatus.DEGRADED and not self.degraded_reasons:
            raise ValueError("degraded evidence requires a reason")
        object.__setattr__(
            self,
            "suppression_metadata",
            tuple(_freeze(value) for value in self.suppression_metadata),
        )
        for name in (
            "baseline_metadata",
            "network_usage",
            "provider_usage",
            "provider_metadata",
        ):
            object.__setattr__(self, name, _freeze(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class StagedSkillCandidate:
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    snapshot_path: Path

    def __post_init__(self) -> None:
        validate_id(self.candidate_id, "skill")
        if self.candidate_revision < 1:
            raise ValueError("candidate_revision must be >= 1")
        _digest(self.candidate_digest, "candidate digest")
        if not isinstance(self.snapshot_path, Path):
            raise TypeError("snapshot_path must be a pathlib.Path")


@dataclass(frozen=True, slots=True)
class RawReportArtifact:
    digest: str
    artifact_ref: str
    size_bytes: int

    def __post_init__(self) -> None:
        _digest(self.digest, "raw-report digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")


class RawSecurityReportStore(Protocol):
    def put(self, payload: bytes) -> RawReportArtifact: ...


class FilesystemRawSecurityReportStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, payload: bytes) -> RawReportArtifact:
        digest = sha256(payload).hexdigest()
        target = self.root / f"{digest}.json"
        if not target.exists():
            fd, temp = tempfile.mkstemp(prefix=f".{digest}.", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp, 0o600)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
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
        self._items: dict[str, SecurityEvidence] = {}

    def add(self, evidence: SecurityEvidence) -> None:
        current = self._items.get(evidence.evidence_id)
        if current is not None and current != evidence:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "security evidence identity is immutable",
            )
        self._items[evidence.evidence_id] = evidence

    def get(self, evidence_id: str) -> SecurityEvidence:
        try:
            return self._items[evidence_id]
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
        values = [value for value in self._items.values() if value.candidate_id == candidate_id]
        if candidate_revision is not None:
            values = [value for value in values if value.candidate_revision == candidate_revision]
        return tuple(sorted(values, key=lambda value: (value.observed_at, value.evidence_id)))

    def list_all(self) -> tuple[SecurityEvidence, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda value: (value.observed_at, value.evidence_id),
            )
        )


class JsonSecurityEvidenceRepository(InMemorySecurityEvidenceRepository):
    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__()
        if path.exists():
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("schema_version") != self.SCHEMA_VERSION
                or not isinstance(raw.get("evidence"), list)
            ):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "invalid security evidence store",
                )
            for item in raw["evidence"]:
                if not isinstance(item, dict):
                    raise ContractError(
                        ErrorCode.INVALID_PROVIDER_RESPONSE,
                        "invalid security evidence item",
                    )
                super().add(security_evidence_from_json(item))

    def add(self, evidence: SecurityEvidence) -> None:
        before = len(self._items)
        super().add(evidence)
        if len(self._items) != before:
            self._persist()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "evidence": [security_evidence_to_json(value) for value in self.list_all()],
        }
        fd, temp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


class SecurityEvidenceService:
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
        if provider.provider_id in self._providers:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"security evidence provider exists: {provider.provider_id}",
            )
        self._providers[provider.provider_id] = provider

    def unregister_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def scan(
        self,
        provider_id: str,
        candidate: StagedSkillCandidate,
    ) -> SecurityEvidence:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"security evidence provider not found: {provider_id}",
            )
        if not provider.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"security evidence provider disabled: {provider_id}",
            )
        evidence = provider.scan(candidate)
        binding = (
            evidence.candidate_id,
            evidence.candidate_revision,
            evidence.candidate_digest,
        )
        expected = (
            candidate.candidate_id,
            candidate.candidate_revision,
            candidate.candidate_digest,
        )
        if evidence.provider != provider_id or binding != expected:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "security evidence binding mismatch",
            )
        self.repository.add(evidence)
        return evidence


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
                "rule_id": finding.rule_id,
                "occurrence_id": finding.occurrence_id,
                "category": finding.category,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "summary": finding.summary,
                "path": finding.path,
                "line": finding.line,
                "metadata": _mapping(finding.metadata),
            }
            for finding in evidence.findings
        ],
        "degraded_reasons": list(evidence.degraded_reasons),
        "suppression_metadata": [_mapping(value) for value in evidence.suppression_metadata],
        "baseline_metadata": _mapping(evidence.baseline_metadata),
        "network_usage": _mapping(evidence.network_usage),
        "provider_usage": _mapping(evidence.provider_usage),
        "provider_metadata": _mapping(evidence.provider_metadata),
        "known_provider_limitations": list(evidence.known_provider_limitations),
        "raw_report_digest": evidence.raw_report_digest,
        "raw_report_artifact_ref": evidence.raw_report_artifact_ref,
    }


def security_evidence_from_json(payload: Mapping[str, object]) -> SecurityEvidence:
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "invalid security evidence findings",
        )
    findings = tuple(_finding(value) for value in raw_findings if isinstance(value, Mapping))
    raw_suppressions = payload.get("suppression_metadata", [])
    suppressions = (
        tuple(_mapping(value) for value in raw_suppressions if isinstance(value, Mapping))
        if isinstance(raw_suppressions, list)
        else ()
    )
    return SecurityEvidence(
        evidence_id=_req_str(payload.get("evidence_id")),
        provider=_req_str(payload.get("provider")),
        provider_version=_req_str(payload.get("provider_version")),
        provider_revision=_req_str(payload.get("provider_revision")),
        provider_build_identity=_req_str(payload.get("provider_build_identity")),
        dependency_set_digest=_req_str(payload.get("dependency_set_digest")),
        scan_mode=_req_str(payload.get("scan_mode")),
        policy_config_revision=_req_str(payload.get("policy_config_revision")),
        observed_at=datetime.fromisoformat(_req_str(payload.get("observed_at"))),
        candidate_id=_req_str(payload.get("candidate_id")),
        candidate_revision=_req_int(payload.get("candidate_revision")),
        candidate_digest=_req_str(payload.get("candidate_digest")),
        status=SecurityEvidenceStatus(_req_str(payload.get("status"))),
        complete=_req_bool(payload.get("complete")),
        findings=findings,
        degraded_reasons=_strings(payload.get("degraded_reasons")),
        suppression_metadata=suppressions,
        baseline_metadata=_mapping(payload.get("baseline_metadata")),
        network_usage=_mapping(payload.get("network_usage")),
        provider_usage=_mapping(payload.get("provider_usage")),
        provider_metadata=_mapping(payload.get("provider_metadata")),
        known_provider_limitations=_strings(payload.get("known_provider_limitations")),
        raw_report_digest=_opt_str(payload.get("raw_report_digest")),
        raw_report_artifact_ref=_opt_str(payload.get("raw_report_artifact_ref")),
    )


def _finding(payload: Mapping[object, object]) -> SecurityFinding:
    return SecurityFinding(
        rule_id=_opt_str(payload.get("rule_id")),
        occurrence_id=_opt_str(payload.get("occurrence_id")),
        category=_opt_str(payload.get("category")),
        severity=_opt_str(payload.get("severity")),
        confidence=_opt_float(payload.get("confidence")),
        summary=_opt_str(payload.get("summary")),
        path=_opt_str(payload.get("path")),
        line=_opt_int(payload.get("line")),
        metadata=_mapping(payload.get("metadata")),
    )


def _mapping(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _json(item) for key, item in value.items()}


def _json(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    return str(value)


def _req_str(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "required string missing",
        )
    return value


def _req_int(value: object) -> int:
    result = _opt_int(value)
    if result is None:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "required integer missing",
        )
    return result


def _req_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "required boolean missing",
        )
    return value


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()
