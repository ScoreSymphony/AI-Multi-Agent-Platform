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
from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


def _digest(value: str, name: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


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
            tuple(MappingProxyType(dict(v)) for v in self.suppression_metadata),
        )
        for name in ("baseline_metadata", "network_usage", "provider_usage", "provider_metadata"):
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
        return RawReportArtifact(digest, f"security-evidence://raw/{digest}", len(payload))


class SecurityEvidenceProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...

    def scan(self, candidate: StagedSkillCandidate) -> SecurityEvidence: ...


class SecurityEvidenceRepository(Protocol):
    def add(self, evidence: SecurityEvidence) -> None: ...
    def get(self, evidence_id: str) -> SecurityEvidence: ...
    def list_for_candidate(self, candidate_id: str, candidate_revision: int | None = None) -> tuple[SecurityEvidence, ...]: ...
    def list_all(self) -> tuple[SecurityEvidence, ...]: ...


class InMemorySecurityEvidenceRepository:
    def __init__(self) -> None:
        self._items: dict[str, SecurityEvidence] = {}

    def add(self, evidence: SecurityEvidence) -> None:
        current = self._items.get(evidence.evidence_id)
        if current is not None and current != evidence:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "security evidence identity is immutable")
        self._items[evidence.evidence_id] = evidence

    def get(self, evidence_id: str) -> SecurityEvidence:
        try:
            return self._items[evidence_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"security evidence not found: {evidence_id}") from exc

    def list_for_candidate(self, candidate_id: str, candidate_revision: int | None = None) -> tuple[SecurityEvidence, ...]:
        values = [v for v in self._items.values() if v.candidate_id == candidate_id]
        if candidate_revision is not None:
            values = [v for v in values if v.candidate_revision == candidate_revision]
        return tuple(sorted(values, key=lambda v: (v.observed_at, v.evidence_id)))

    def list_all(self) -> tuple[SecurityEvidence, ...]:
        return tuple(sorted(self._items.values(), key=lambda v: (v.observed_at, v.evidence_id)))


class JsonSecurityEvidenceRepository(InMemorySecurityEvidenceRepository):
    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__()
        if path.exists():
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("evidence"), list):
                raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence store")
            for item in raw["evidence"]:
                if not isinstance(item, dict):
                    raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence item")
                super().add(security_evidence_from_json(item))

    def add(self, evidence: SecurityEvidence) -> None:
        before = len(self._items)
        super().add(evidence)
        if len(self._items) != before:
            self._persist()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "evidence": [security_evidence_to_json(v) for v in self.list_all()]}
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
    def __init__(self, repository: SecurityEvidenceRepository, providers: tuple[SecurityEvidenceProvider, ...] = ()) -> None:
        self.repository = repository
        self._providers: dict[str, SecurityEvidenceProvider] = {}
        for provider in providers:
            self.register_provider(provider)

    def register_provider(self, provider: SecurityEvidenceProvider) -> None:
        if provider.provider_id in self._providers:
            raise ContractError(ErrorCode.CONFLICT, f"security evidence provider exists: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def unregister_provider(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def scan(self, provider_id: str, candidate: StagedSkillCandidate) -> SecurityEvidence:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"security evidence provider not found: {provider_id}")
        if not provider.enabled:
            raise ContractError(ErrorCode.UNAVAILABLE, f"security evidence provider disabled: {provider_id}")
        evidence = provider.scan(candidate)
        if evidence.provider != provider_id or (evidence.candidate_id, evidence.candidate_revision, evidence.candidate_digest) != (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest):
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "security evidence binding mismatch")
        self.repository.add(evidence)
        return evidence


def new_security_evidence_id() -> str:
    return new_id("security_evidence")


def security_evidence_to_json(e: SecurityEvidence) -> dict[str, JsonValue]:
    return {
        "evidence_id": e.evidence_id, "provider": e.provider, "provider_version": e.provider_version,
        "provider_revision": e.provider_revision, "provider_build_identity": e.provider_build_identity,
        "dependency_set_digest": e.dependency_set_digest, "scan_mode": e.scan_mode,
        "policy_config_revision": e.policy_config_revision, "observed_at": e.observed_at.isoformat(),
        "candidate_id": e.candidate_id, "candidate_revision": e.candidate_revision,
        "candidate_digest": e.candidate_digest, "status": e.status.value, "complete": e.complete,
        "findings": [{"rule_id": f.rule_id, "occurrence_id": f.occurrence_id, "category": f.category,
            "severity": f.severity, "confidence": f.confidence, "summary": f.summary, "path": f.path,
            "line": f.line, "metadata": dict(f.metadata)} for f in e.findings],
        "degraded_reasons": list(e.degraded_reasons),
        "suppression_metadata": [dict(v) for v in e.suppression_metadata],
        "baseline_metadata": dict(e.baseline_metadata), "network_usage": dict(e.network_usage),
        "provider_usage": dict(e.provider_usage), "provider_metadata": dict(e.provider_metadata),
        "known_provider_limitations": list(e.known_provider_limitations),
        "raw_report_digest": e.raw_report_digest, "raw_report_artifact_ref": e.raw_report_artifact_ref,
    }


def security_evidence_from_json(p: Mapping[str, object]) -> SecurityEvidence:
    raw_findings = p.get("findings", [])
    if not isinstance(raw_findings, list):
        raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, "invalid security evidence findings")
    findings = tuple(_finding(v) for v in raw_findings if isinstance(v, Mapping))
    raw_suppressions = p.get("suppression_metadata", [])
    suppressions = tuple(_mapping(v) for v in raw_suppressions if isinstance(v, Mapping)) if isinstance(raw_suppressions, list) else ()
    return SecurityEvidence(
        evidence_id=_req_str(p.get("evidence_id")), provider=_req_str(p.get("provider")),
        provider_version=_req_str(p.get("provider_version")), provider_revision=_req_str(p.get("provider_revision")),
        provider_build_identity=_req_str(p.get("provider_build_identity")), dependency_set_digest=_req_str(p.get("dependency_set_digest")),
        scan_mode=_req_str(p.get("scan_mode")), policy_config_revision=_req_str(p.get("policy_config_revision")),
        observed_at=datetime.fromisoformat(_req_str(p.get("observed_at"))), candidate_id=_req_str(p.get("candidate_id")),
        candidate_revision=_req_int(p.get("candidate_revision")), candidate_digest=_req_str(p.get("candidate_digest")),
        status=SecurityEvidenceStatus(_req_str(p.get("status"))), complete=_req_bool(p.get("complete")), findings=findings,
        degraded_reasons=_strings(p.get("degraded_reasons")), suppression_metadata=suppressions,
        baseline_metadata=_mapping(p.get("baseline_metadata")), network_usage=_mapping(p.get("network_usage")),
        provider_usage=_mapping(p.get("provider_usage")), provider_metadata=_mapping(p.get("provider_metadata")),
        known_provider_limitations=_strings(p.get("known_provider_limitations")), raw_report_digest=_opt_str(p.get("raw_report_digest")),
        raw_report_artifact_ref=_opt_str(p.get("raw_report_artifact_ref")),
    )


def _finding(p: Mapping[object, object]) -> SecurityFinding:
    return SecurityFinding(_opt_str(p.get("rule_id")), _opt_str(p.get("occurrence_id")), _opt_str(p.get("category")),
        _opt_str(p.get("severity")), _opt_float(p.get("confidence")), _opt_str(p.get("summary")), _opt_str(p.get("path")),
        _opt_int(p.get("line")), _mapping(p.get("metadata")))


def _mapping(value: object) -> dict[str, JsonValue]:
    return {str(k): _json(v) for k, v in value.items()} if isinstance(value, Mapping) else {}


def _json(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool): return value
    if isinstance(value, list | tuple): return [_json(v) for v in value]
    if isinstance(value, Mapping): return {str(k): _json(v) for k, v in value.items()}
    return str(value)


def _req_str(value: object) -> str:
    if not isinstance(value, str) or not value: raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, "required string missing")
    return value


def _req_int(value: object) -> int:
    result = _opt_int(value)
    if result is None: raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, "required integer missing")
    return result


def _req_bool(value: object) -> bool:
    if not isinstance(value, bool): raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, "required boolean missing")
    return value


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool): return None
    if isinstance(value, int): return value
    if isinstance(value, float): return int(value)
    if isinstance(value, str):
        try: return int(value)
        except ValueError: return None
    return None


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool): return None
    if isinstance(value, int | float): return float(value)
    if isinstance(value, str):
        try: return float(value)
        except ValueError: return None
    return None


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(v) for v in value) if isinstance(value, list | tuple) else ()
