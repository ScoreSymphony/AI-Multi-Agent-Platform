"""Canonical Research repository boundary and restart-safe reference implementation."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .models import (
    Claim,
    ClaimConfidence,
    ClaimStatus,
    EvidenceRecord,
    EvidenceRelation,
    FreshnessPolicy,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    ResearchStatus,
    ResearchVerificationBinding,
    ResearchVerificationSubjectType,
    SourceObservation,
    SourceObservationState,
    SourceRecord,
)

RESEARCH_PERSISTENCE_SCHEMA_VERSION = "1"


class ResearchRepository(Protocol):
    def create_item(self, item: ResearchItem) -> ResearchItem: ...
    def get_item(self, research_item_id: str) -> ResearchItem: ...
    def save_item(self, item: ResearchItem, *, expected_revision: int) -> ResearchItem: ...
    def list_items(self) -> tuple[ResearchItem, ...]: ...
    def create_source(self, source: SourceRecord) -> SourceRecord: ...
    def get_source(self, source_id: str) -> SourceRecord: ...
    def save_source(self, source: SourceRecord) -> SourceRecord: ...
    def create_observation(self, observation: SourceObservation) -> SourceObservation: ...
    def get_observation(self, observation_id: str) -> SourceObservation: ...
    def list_observations(self, source_id: str) -> tuple[SourceObservation, ...]: ...
    def observation_for_idempotency(
        self, source_id: str, key: str
    ) -> SourceObservation | None: ...
    def create_claim(self, claim: Claim) -> Claim: ...
    def get_claim(self, claim_id: str) -> Claim: ...
    def save_claim(self, claim: Claim, *, expected_revision: int) -> Claim: ...
    def list_claims(self, research_item_id: str) -> tuple[Claim, ...]: ...
    def create_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord: ...
    def get_evidence(self, evidence_id: str) -> EvidenceRecord: ...
    def list_evidence(self, research_item_id: str) -> tuple[EvidenceRecord, ...]: ...
    def create_verification_binding(
        self, binding: ResearchVerificationBinding
    ) -> ResearchVerificationBinding: ...
    def list_verification_bindings(
        self, research_item_id: str
    ) -> tuple[ResearchVerificationBinding, ...]: ...


class InMemoryResearchRepository:
    """Optimistic, append-only reference repository for Research evidence history."""

    def __init__(self) -> None:
        self._items: dict[str, ResearchItem] = {}
        self._sources: dict[str, SourceRecord] = {}
        self._observations: dict[str, SourceObservation] = {}
        self._claims: dict[str, Claim] = {}
        self._evidence: dict[str, EvidenceRecord] = {}
        self._bindings: dict[str, ResearchVerificationBinding] = {}
        self._lock = threading.RLock()

    def _after_mutation(self) -> None:
        pass

    def create_item(self, item: ResearchItem) -> ResearchItem:
        with self._lock:
            if item.research_item_id in self._items:
                raise ContractError(ErrorCode.CONFLICT, "Research Item already exists")
            self._items[item.research_item_id] = item
            self._after_mutation()
            return item

    def get_item(self, research_item_id: str) -> ResearchItem:
        with self._lock:
            try:
                return self._items[research_item_id]
            except KeyError as exc:
                raise ContractError(ErrorCode.NOT_FOUND, "Research Item was not found") from exc

    def save_item(self, item: ResearchItem, *, expected_revision: int) -> ResearchItem:
        with self._lock:
            current = self.get_item(item.research_item_id)
            if current.revision != expected_revision:
                raise ContractError(ErrorCode.CONFLICT, "Research Item revision changed")
            if item.revision != expected_revision + 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Research Item revision must increase exactly once",
                )
            self._items[item.research_item_id] = item
            self._after_mutation()
            return item

    def list_items(self) -> tuple[ResearchItem, ...]:
        with self._lock:
            return tuple(sorted(self._items.values(), key=lambda value: value.created_at))

    def create_source(self, source: SourceRecord) -> SourceRecord:
        with self._lock:
            self.get_item(source.research_item_id)
            if source.source_id in self._sources:
                raise ContractError(ErrorCode.CONFLICT, "Research Source already exists")
            self._sources[source.source_id] = source
            self._after_mutation()
            return source

    def get_source(self, source_id: str) -> SourceRecord:
        with self._lock:
            try:
                return self._sources[source_id]
            except KeyError as exc:
                raise ContractError(ErrorCode.NOT_FOUND, "Research Source was not found") from exc

    def save_source(self, source: SourceRecord) -> SourceRecord:
        with self._lock:
            if source.source_id not in self._sources:
                raise ContractError(ErrorCode.NOT_FOUND, "Research Source was not found")
            self._sources[source.source_id] = source
            self._after_mutation()
            return source

    def create_observation(self, observation: SourceObservation) -> SourceObservation:
        with self._lock:
            source = self.get_source(observation.source_id)
            if source.research_item_id != observation.research_item_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Source Observation belongs to a different Research Item",
                )
            if observation.idempotency_key is not None:
                existing = self.observation_for_idempotency(
                    observation.source_id, observation.idempotency_key
                )
                if existing is not None:
                    if _observation_equivalent(existing, observation):
                        return existing
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "Research source observation idempotency key was reused with changed content",
                    )
            if observation.observation_id in self._observations:
                raise ContractError(ErrorCode.CONFLICT, "Source Observation already exists")
            self._observations[observation.observation_id] = observation
            self._after_mutation()
            return observation

    def get_observation(self, observation_id: str) -> SourceObservation:
        with self._lock:
            try:
                return self._observations[observation_id]
            except KeyError as exc:
                raise ContractError(ErrorCode.NOT_FOUND, "Source Observation was not found") from exc

    def list_observations(self, source_id: str) -> tuple[SourceObservation, ...]:
        with self._lock:
            values = [value for value in self._observations.values() if value.source_id == source_id]
            return tuple(sorted(values, key=lambda value: value.retrieved_at))

    def observation_for_idempotency(self, source_id: str, key: str) -> SourceObservation | None:
        with self._lock:
            for value in self._observations.values():
                if value.source_id == source_id and value.idempotency_key == key:
                    return value
            return None

    def create_claim(self, claim: Claim) -> Claim:
        with self._lock:
            self.get_item(claim.research_item_id)
            if claim.claim_id in self._claims:
                raise ContractError(ErrorCode.CONFLICT, "Research Claim already exists")
            self._claims[claim.claim_id] = claim
            self._after_mutation()
            return claim

    def get_claim(self, claim_id: str) -> Claim:
        with self._lock:
            try:
                return self._claims[claim_id]
            except KeyError as exc:
                raise ContractError(ErrorCode.NOT_FOUND, "Research Claim was not found") from exc

    def save_claim(self, claim: Claim, *, expected_revision: int) -> Claim:
        with self._lock:
            current = self.get_claim(claim.claim_id)
            if current.revision != expected_revision:
                raise ContractError(ErrorCode.CONFLICT, "Research Claim revision changed")
            if claim.revision != expected_revision + 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Research Claim revision must increase exactly once",
                )
            self._claims[claim.claim_id] = claim
            self._after_mutation()
            return claim

    def list_claims(self, research_item_id: str) -> tuple[Claim, ...]:
        with self._lock:
            return tuple(
                value for value in self._claims.values() if value.research_item_id == research_item_id
            )

    def create_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        with self._lock:
            claim = self.get_claim(evidence.claim_id)
            observation = self.get_observation(evidence.source_observation_id)
            if claim.research_item_id != evidence.research_item_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Evidence Claim belongs to a different Research Item",
                )
            if observation.research_item_id != evidence.research_item_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Evidence Source Observation belongs to a different Research Item",
                )
            if observation.source_id != evidence.source_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Evidence Source does not match Source Observation",
                )
            if evidence.evidence_id in self._evidence:
                existing = self._evidence[evidence.evidence_id]
                if existing == evidence:
                    return existing
                raise ContractError(ErrorCode.CONFLICT, "Research Evidence already exists")
            self._evidence[evidence.evidence_id] = evidence
            self._after_mutation()
            return evidence

    def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        with self._lock:
            try:
                return self._evidence[evidence_id]
            except KeyError as exc:
                raise ContractError(ErrorCode.NOT_FOUND, "Research Evidence was not found") from exc

    def list_evidence(self, research_item_id: str) -> tuple[EvidenceRecord, ...]:
        with self._lock:
            return tuple(
                value
                for value in self._evidence.values()
                if value.research_item_id == research_item_id
            )

    def create_verification_binding(
        self, binding: ResearchVerificationBinding
    ) -> ResearchVerificationBinding:
        with self._lock:
            for existing in self._bindings.values():
                if existing.verification_id == binding.verification_id:
                    if (
                        existing.subject_type == binding.subject_type
                        and existing.subject_id == binding.subject_id
                        and existing.subject_revision == binding.subject_revision
                        and existing.subject_digest == binding.subject_digest
                    ):
                        return existing
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "Verification is already bound to a different Research revision",
                    )
            self._bindings[binding.binding_id] = binding
            self._after_mutation()
            return binding

    def list_verification_bindings(
        self, research_item_id: str
    ) -> tuple[ResearchVerificationBinding, ...]:
        with self._lock:
            return tuple(
                value
                for value in self._bindings.values()
                if value.research_item_id == research_item_id
            )


class SqliteResearchRepository(InMemoryResearchRepository):
    """Dependency-free durable single-node Research repository."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._restoring = True
        super().__init__()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._restore()
        self._restoring = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS research_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
                )
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to initialize Research store") from exc

    def _after_mutation(self) -> None:
        if not self._restoring:
            self._persist()

    def _persist(self) -> None:
        document = {
            "schema_version": RESEARCH_PERSISTENCE_SCHEMA_VERSION,
            "items": [_encode(value) for value in self._items.values()],
            "sources": [_encode(value) for value in self._sources.values()],
            "observations": [_encode(value) for value in self._observations.values()],
            "claims": [_encode(value) for value in self._claims.values()],
            "evidence": [_encode(value) for value in self._evidence.values()],
            "bindings": [_encode(value) for value in self._bindings.values()],
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO research_state(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (encoded,),
                )
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist Research state") from exc

    def _restore(self) -> None:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT payload FROM research_state WHERE id=1").fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to restore Research state") from exc
        if row is None:
            return
        try:
            raw = json.loads(cast(str, row["payload"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "persisted Research state is invalid") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != RESEARCH_PERSISTENCE_SCHEMA_VERSION:
            raise ContractError(ErrorCode.BACKEND_ERROR, "unsupported Research persistence schema")
        self._items = _decode_map(raw, "items", ResearchItem, "research_item_id")
        self._sources = _decode_map(raw, "sources", SourceRecord, "source_id")
        self._observations = _decode_map(raw, "observations", SourceObservation, "observation_id")
        self._claims = _decode_map(raw, "claims", Claim, "claim_id")
        self._evidence = _decode_map(raw, "evidence", EvidenceRecord, "evidence_id")
        self._bindings = _decode_map(raw, "bindings", ResearchVerificationBinding, "binding_id")


def _observation_equivalent(left: SourceObservation, right: SourceObservation) -> bool:
    return (
        left.source_id == right.source_id
        and left.research_item_id == right.research_item_id
        and left.binding == right.binding
        and left.identity_proven == right.identity_proven
        and left.repository_id == right.repository_id
        and left.requested_repository_revision == right.requested_repository_revision
        and left.intelligence_provider_id == right.intelligence_provider_id
    )


_DATACLASSES: dict[str, type[Any]] = {
    cls.__name__: cls
    for cls in (
        OwnerRef,
        Provenance,
        FreshnessPolicy,
        ResearchItem,
        SourceRecord,
        SourceObservation,
        Claim,
        EvidenceRecord,
        ResearchVerificationBinding,
    )
}
_ENUMS: dict[str, type[Enum]] = {
    cls.__name__: cls
    for cls in (
        ResearchClass,
        ResearchStatus,
        ResearchSourceType,
        SourceObservationState,
        ClaimStatus,
        ClaimConfidence,
        EvidenceRelation,
        ResearchVerificationSubjectType,
    )
}


def _encode(value: object) -> object:
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, Enum):
        return {"__enum__": type(value).__name__, "value": value.value}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": type(value).__name__,
            "fields": {item.name: _encode(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    return value


def _decode(value: object) -> object:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "__datetime__" in value:
        raw = value["__datetime__"]
        if not isinstance(raw, str):
            raise ValueError("invalid persisted datetime")
        return datetime.fromisoformat(raw)
    if "__enum__" in value:
        name = value["__enum__"]
        raw = value.get("value")
        if not isinstance(name, str):
            raise ValueError("invalid persisted enum")
        enum_type = _ENUMS[name]
        return enum_type(raw)
    if "__tuple__" in value:
        raw = value["__tuple__"]
        if not isinstance(raw, list):
            raise ValueError("invalid persisted tuple")
        return tuple(_decode(item) for item in raw)
    if "__dataclass__" in value:
        name = value["__dataclass__"]
        raw_fields = value.get("fields")
        if not isinstance(name, str) or not isinstance(raw_fields, dict):
            raise ValueError("invalid persisted dataclass")
        cls = _DATACLASSES[name]
        kwargs = {str(key): _decode(item) for key, item in raw_fields.items()}
        return cls(**kwargs)
    return {str(key): _decode(item) for key, item in value.items()}


def _decode_map(
    raw: Mapping[str, object],
    key: str,
    expected: type[Any],
    id_field: str,
) -> dict[str, Any]:
    values = raw.get(key)
    if not isinstance(values, list):
        raise ContractError(ErrorCode.BACKEND_ERROR, f"persisted Research {key} is invalid")
    result: dict[str, Any] = {}
    try:
        for encoded in values:
            value = _decode(encoded)
            if not isinstance(value, expected):
                raise ValueError(f"unexpected type in {key}")
            identifier = getattr(value, id_field)
            if not isinstance(identifier, str) or identifier in result:
                raise ValueError(f"invalid or duplicate identity in {key}")
            result[identifier] = value
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.BACKEND_ERROR, f"persisted Research {key} is invalid") from exc
    return result
