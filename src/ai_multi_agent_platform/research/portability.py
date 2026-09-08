"""Provenance-preserving Research bundle export/import for issue #589.

The bundle is historical evidence state, not an executable workflow. Import preserves exact
Research identities and source-observation bindings. #86 Verification authority is never inferred
from serialized Research metadata: verification bindings are restored only through an explicit
local validator supplied by the importing deployment.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue

from .models import (
    Claim,
    EvidenceRecord,
    ResearchItem,
    ResearchVerificationBinding,
    SourceObservation,
    SourceRecord,
)
from .repository import ResearchRepository, _decode, _encode
from .service import ResearchService

RESEARCH_BUNDLE_SCHEMA_VERSION = "1.0"
RESEARCH_BUNDLE_KIND = "research-evidence-bundle"

VerificationBindingValidator = Callable[[ResearchVerificationBinding], bool]


def export_research_bundle(
    research: ResearchService,
    research_item_id: str,
) -> dict[str, JsonValue]:
    """Export one complete ResearchItem evidence graph without activating anything downstream."""

    item = research.repository.get_item(research_item_id)
    sources = tuple(research.repository.get_source(source_id) for source_id in item.source_ids)
    observations = tuple(
        observation
        for source in sources
        for observation in research.repository.list_observations(source.source_id)
    )
    claims = tuple(research.repository.get_claim(claim_id) for claim_id in item.claim_ids)
    evidence = tuple(research.repository.get_evidence(value) for value in item.evidence_ids)
    bindings = research.repository.list_verification_bindings(research_item_id)
    return {
        "schema_version": RESEARCH_BUNDLE_SCHEMA_VERSION,
        "kind": RESEARCH_BUNDLE_KIND,
        "research_item": cast(JsonValue, _encode(item)),
        "sources": cast(JsonValue, [_encode(value) for value in sources]),
        "observations": cast(JsonValue, [_encode(value) for value in observations]),
        "claims": cast(JsonValue, [_encode(value) for value in claims]),
        "evidence": cast(JsonValue, [_encode(value) for value in evidence]),
        "verification_bindings": cast(JsonValue, [_encode(value) for value in bindings]),
        "activation_semantics": "none",
        "verification_semantics": "local-revalidation-required",
    }


def import_research_bundle(
    research: ResearchService,
    bundle: dict[str, JsonValue],
    *,
    verification_binding_validator: VerificationBindingValidator | None = None,
) -> str:
    """Import exact historical Research records through the owning repository.

    All records are decoded and cross-validated before the destination is touched. Existing exact
    records are treated as idempotent. A conflicting ID fails closed. Serialized #86 bindings
    require an explicit deployment-owned validator and are never trusted merely because they were
    present in the bundle.
    """

    if bundle.get("schema_version") != RESEARCH_BUNDLE_SCHEMA_VERSION:
        raise ContractError(ErrorCode.INVALID_REQUEST, "unsupported Research bundle version")
    if bundle.get("kind") != RESEARCH_BUNDLE_KIND:
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Research bundle kind")
    if bundle.get("activation_semantics") != "none":
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Research bundle must not carry downstream activation semantics",
        )

    item = _decoded(bundle.get("research_item"), ResearchItem, "research_item")
    sources = _decoded_array(bundle.get("sources"), SourceRecord, "sources")
    observations = _decoded_array(bundle.get("observations"), SourceObservation, "observations")
    claims = _decoded_array(bundle.get("claims"), Claim, "claims")
    evidence = _decoded_array(bundle.get("evidence"), EvidenceRecord, "evidence")
    bindings = _decoded_array(
        bundle.get("verification_bindings"),
        ResearchVerificationBinding,
        "verification_bindings",
    )

    _validate_bundle_graph(item, sources, observations, claims, evidence, bindings)
    if bindings and verification_binding_validator is None:
        raise ContractError(
            ErrorCode.CONFLICT,
            (
                "Research bundle contains #86 bindings but no local "
                "Verification validator was supplied"
            ),
        )
    for binding in bindings:
        assert verification_binding_validator is not None
        if not verification_binding_validator(binding):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Research bundle references a Verification that is not valid in this deployment",
                details={"verification_id": binding.verification_id},
            )

    _preflight_destination(
        research.repository,
        item,
        sources,
        observations,
        claims,
        evidence,
        bindings,
    )
    _apply_if_missing(
        research.repository,
        item,
        sources,
        observations,
        claims,
        evidence,
        bindings,
    )
    return item.research_item_id


def _validate_bundle_graph(
    item: ResearchItem,
    sources: tuple[SourceRecord, ...],
    observations: tuple[SourceObservation, ...],
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRecord, ...],
    bindings: tuple[ResearchVerificationBinding, ...],
) -> None:
    source_by_id = _unique(sources, "source_id", "Source")
    observation_by_id = _unique(observations, "observation_id", "SourceObservation")
    claim_by_id = _unique(claims, "claim_id", "Claim")
    evidence_by_id = _unique(evidence, "evidence_id", "Evidence")
    _unique(bindings, "binding_id", "Verification binding")

    if set(item.source_ids) != set(source_by_id):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION, "Research bundle Source graph is incomplete"
        )
    if set(item.claim_ids) != set(claim_by_id):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION, "Research bundle Claim graph is incomplete"
        )
    if set(item.evidence_ids) != set(evidence_by_id):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Research bundle Evidence graph is incomplete",
        )

    for source in sources:
        if source.research_item_id != item.research_item_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Source belongs to another item",
            )
        source_observation_ids = {
            value.observation_id for value in observations if value.source_id == source.source_id
        }
        if set(source.observation_ids) != source_observation_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Source observation history is incomplete",
            )
        if (
            source.current_observation_id is not None
            and source.current_observation_id not in observation_by_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Source current observation is missing from bundle",
            )
    for observation in observations:
        resolved_source = source_by_id.get(observation.source_id)
        if resolved_source is None or observation.research_item_id != item.research_item_id:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "orphan Research SourceObservation")
    for claim in claims:
        if claim.research_item_id != item.research_item_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Claim belongs to another item",
            )
        if any(value not in evidence_by_id for value in claim.evidence_ids):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Claim references missing Evidence",
            )
    for value in evidence:
        resolved_claim = claim_by_id.get(value.claim_id)
        resolved_observation = observation_by_id.get(value.source_observation_id)
        if (
            resolved_claim is None
            or resolved_observation is None
            or value.source_id not in source_by_id
        ):
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "orphan Research Evidence")
        if value.research_item_id != item.research_item_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Evidence belongs to another item",
            )
        if resolved_observation.source_id != value.source_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Evidence source binding is invalid",
            )
        expected_binding = (
            value.source_revision,
            value.source_version,
            value.source_commit,
            value.source_etag,
            value.source_content_digest,
            value.source_snapshot_digest,
        )
        if expected_binding != resolved_observation.binding[:6]:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Evidence does not match its exact SourceObservation binding",
            )
    for binding in bindings:
        if binding.research_item_id != item.research_item_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Verification binding belongs to another item",
            )


def _preflight_destination(
    repository: ResearchRepository,
    item: ResearchItem,
    sources: tuple[SourceRecord, ...],
    observations: tuple[SourceObservation, ...],
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRecord, ...],
    bindings: tuple[ResearchVerificationBinding, ...],
) -> None:
    _same_or_missing(lambda: repository.get_item(item.research_item_id), item, "Research Item")
    for source in sources:
        _same_or_missing(
            partial(repository.get_source, source.source_id),
            source,
            "Source",
        )
    for observation in observations:
        _same_or_missing(
            partial(repository.get_observation, observation.observation_id),
            observation,
            "SourceObservation",
        )
    for claim in claims:
        _same_or_missing(partial(repository.get_claim, claim.claim_id), claim, "Claim")
    for value in evidence:
        _same_or_missing(
            partial(repository.get_evidence, value.evidence_id),
            value,
            "Evidence",
        )
    existing_bindings = {
        value.binding_id: value
        for value in repository.list_verification_bindings(item.research_item_id)
    }
    for binding in bindings:
        existing = existing_bindings.get(binding.binding_id)
        if existing is not None and existing != binding:
            raise ContractError(ErrorCode.CONFLICT, "Research Verification binding ID conflicts")


def _apply_if_missing(
    repository: ResearchRepository,
    item: ResearchItem,
    sources: tuple[SourceRecord, ...],
    observations: tuple[SourceObservation, ...],
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRecord, ...],
    bindings: tuple[ResearchVerificationBinding, ...],
) -> None:
    if not _exists(lambda: repository.get_item(item.research_item_id)):
        repository.create_item(item)
    for source in sources:
        if not _exists(partial(repository.get_source, source.source_id)):
            repository.create_source(source)
    for observation in observations:
        if not _exists(partial(repository.get_observation, observation.observation_id)):
            repository.create_observation(observation)
    for claim in claims:
        if not _exists(partial(repository.get_claim, claim.claim_id)):
            repository.create_claim(claim)
    for value in evidence:
        if not _exists(partial(repository.get_evidence, value.evidence_id)):
            repository.create_evidence(value)
    existing_binding_ids = {
        value.binding_id for value in repository.list_verification_bindings(item.research_item_id)
    }
    for binding in bindings:
        if binding.binding_id not in existing_binding_ids:
            repository.create_verification_binding(binding)


def _same_or_missing(loader: Callable[[], object], expected: object, label: str) -> None:
    try:
        existing = loader()
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    if existing != expected:
        raise ContractError(ErrorCode.CONFLICT, f"portable {label} ID has different content")


def _exists(loader: Callable[[], object]) -> bool:
    try:
        loader()
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return False
        raise
    return True


def _decoded[T](value: JsonValue | None, expected: type[T], label: str) -> T:
    try:
        decoded = _decode(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST, f"invalid portable Research {label}"
        ) from exc
    if not isinstance(decoded, expected):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"invalid portable Research {label}")
    return decoded


def _decoded_array[T](value: JsonValue | None, expected: type[T], label: str) -> tuple[T, ...]:
    if not isinstance(value, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST, f"portable Research {label} must be an array"
        )
    return tuple(_decoded(item, expected, label) for item in value)


def _unique[T](values: tuple[T, ...], attribute: str, label: str) -> dict[str, T]:
    result: dict[str, T] = {}
    for value in values:
        identifier = getattr(value, attribute)
        if not isinstance(identifier, str) or identifier in result:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"duplicate {label} identity")
        result[identifier] = value
    return result


__all__ = [
    "RESEARCH_BUNDLE_KIND",
    "RESEARCH_BUNDLE_SCHEMA_VERSION",
    "VerificationBindingValidator",
    "export_research_bundle",
    "import_research_bundle",
]
