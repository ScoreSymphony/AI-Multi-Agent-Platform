"""Registration-based Control Plane surface for canonical Research Evidence (#589)."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import ActorIdentity, ActorType, infer_actor_identity, redact_sensitive

from .models import (
    ClaimConfidence,
    EvidenceRelation,
    FreshnessPolicy,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    SourceObservation,
    SourceRecord,
)
from .service import ResearchService

RESEARCH_ITEM_COLLECTION = "research-items"
RESEARCH_SOURCE_COLLECTION = "research-sources"
RESEARCH_OBSERVATION_COLLECTION = "research-source-observations"
RESEARCH_CLAIM_COLLECTION = "research-claims"
RESEARCH_EVIDENCE_COLLECTION = "research-evidence"
RESEARCH_COLLECTIONS = (
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_EVIDENCE_COLLECTION,
)
RESEARCH_COMMANDS = (
    "research.create",
    "research.source.add",
    "research.source.observe",
    "research.claim.add",
    "research.evidence.add",
    "research.evidence.revalidate",
)


def default_actor_resolver(context: RequestContext) -> ActorIdentity:
    """Translate the authenticated northbound actor into canonical #15 identity."""

    if context.actor.actor_type is None:
        return infer_actor_identity(context.actor.principal_ref)
    try:
        actor_type = ActorType(context.actor.actor_type)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor type is not recognized by Research security",
        ) from exc
    return ActorIdentity(context.actor.principal_ref, actor_type)


class ResearchItemResourceService(ResourceService):
    def __init__(self, research: ResearchService) -> None:
        self._research = research

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return tuple(
            _item_resource(self._research, item)
            for item in self._research.repository.list_items()
            if _visible(item, context)
        )

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        item = self._research.repository.get_item(resource_id)
        _require_visible(item, context)
        return _item_resource(self._research, item)


class ResearchSourceResourceService(ResourceService):
    def __init__(self, research: ResearchService) -> None:
        self._research = research

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        values: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            if not _visible(item, context):
                continue
            values.extend(
                _source_resource(self._research, self._research.repository.get_source(source_id))
                for source_id in item.source_ids
            )
        return tuple(values)

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        source = self._research.repository.get_source(resource_id)
        _require_visible(self._research.repository.get_item(source.research_item_id), context)
        return _source_resource(self._research, source)


class ResearchObservationResourceService(ResourceService):
    def __init__(self, research: ResearchService) -> None:
        self._research = research

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        values: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            if not _visible(item, context):
                continue
            for source_id in item.source_ids:
                values.extend(
                    _observation_resource(observation)
                    for observation in self._research.repository.list_observations(source_id)
                )
        return tuple(values)

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        observation = self._research.repository.get_observation(resource_id)
        _require_visible(self._research.repository.get_item(observation.research_item_id), context)
        return _observation_resource(observation)


class ResearchClaimResourceService(ResourceService):
    def __init__(self, research: ResearchService) -> None:
        self._research = research

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        values: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            if _visible(item, context):
                values.extend(
                    _claim_resource(claim)
                    for claim in self._research.repository.list_claims(item.research_item_id)
                )
        return tuple(values)

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        claim = self._research.repository.get_claim(resource_id)
        _require_visible(self._research.repository.get_item(claim.research_item_id), context)
        return _claim_resource(claim)


class ResearchEvidenceResourceService(ResourceService):
    def __init__(self, research: ResearchService) -> None:
        self._research = research

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        values: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            if _visible(item, context):
                values.extend(
                    _evidence_resource(self._research, evidence.evidence_id)
                    for evidence in self._research.repository.list_evidence(item.research_item_id)
                )
        return tuple(values)

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        evidence = self._research.repository.get_evidence(resource_id)
        _require_visible(self._research.repository.get_item(evidence.research_item_id), context)
        return _evidence_resource(self._research, resource_id)


def register_research_control_plane(control_plane: ControlPlane, research: ResearchService) -> None:
    """Expose auditable Research resources without creating a second execution runtime."""

    control_plane.register_resource_service(
        RESEARCH_ITEM_COLLECTION, ResearchItemResourceService(research)
    )
    control_plane.register_resource_service(
        RESEARCH_SOURCE_COLLECTION, ResearchSourceResourceService(research)
    )
    control_plane.register_resource_service(
        RESEARCH_OBSERVATION_COLLECTION, ResearchObservationResourceService(research)
    )
    control_plane.register_resource_service(
        RESEARCH_CLAIM_COLLECTION, ResearchClaimResourceService(research)
    )
    control_plane.register_resource_service(
        RESEARCH_EVIDENCE_COLLECTION, ResearchEvidenceResourceService(research)
    )

    async def create_item(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, RESEARCH_ITEM_COLLECTION)
        owner = _owner_ref(context)
        project_id = _optional_string(payload.get("project_id"), "project_id")
        item = await research.create_item(
            title=_required_string(payload, "title"),
            question=_required_string(payload, "question"),
            research_class=_enum(ResearchClass, payload, "research_class"),
            owner_ref=owner,
            project_id=project_id,
            workspace_id=_optional_string(payload.get("workspace_id"), "workspace_id"),
            task_id=_optional_string(payload.get("task_id"), "task_id"),
            plan_id=_optional_string(payload.get("plan_id"), "plan_id"),
            run_id=_optional_string(payload.get("run_id"), "run_id"),
            data_class=_optional_string(payload.get("data_class"), "data_class") or "standard",
            constraints=_string_tuple(payload.get("constraints"), "constraints"),
            freshness_policy=_freshness_policy(payload.get("freshness_policy")),
            metadata=_metadata(payload.get("metadata")),
            actor=default_actor_resolver(context),
            operation=_operation_context(context, project_id),
        )
        return _item_resource(research, item)

    async def add_source(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        item = research.repository.get_item(resource_ref)
        source = await research.add_source(
            resource_ref,
            source_type=_enum(ResearchSourceType, payload, "source_type"),
            locator=_required_string(payload, "locator"),
            title=_required_string(payload, "title"),
            author=_optional_string(payload.get("author"), "author"),
            publisher=_optional_string(payload.get("publisher"), "publisher"),
            license_ref=_optional_string(payload.get("license_ref"), "license_ref"),
            trust_classification=(
                _optional_string(payload.get("trust_classification"), "trust_classification")
                or "unclassified"
            ),
            metadata=_metadata(payload.get("metadata")),
            actor=default_actor_resolver(context),
            operation=_operation_context(context, item.project_id),
        )
        return _source_resource(research, source)

    async def observe_source(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        source = research.repository.get_source(resource_ref)
        item = research.repository.get_item(source.research_item_id)
        observation = await research.observe_source(
            resource_ref,
            retrieved_at=_datetime(payload, "retrieved_at"),
            idempotency_key=context.idempotency_key,
            revision=_optional_string(payload.get("revision"), "revision"),
            version=_optional_string(payload.get("version"), "version"),
            commit=_optional_string(payload.get("commit"), "commit"),
            etag=_optional_string(payload.get("etag"), "etag"),
            content_digest=_optional_string(payload.get("content_digest"), "content_digest"),
            snapshot_digest=_optional_string(payload.get("snapshot_digest"), "snapshot_digest"),
            snapshot_artifact_id=_optional_string(
                payload.get("snapshot_artifact_id"), "snapshot_artifact_id"
            ),
            identity_proven=_optional_bool(payload.get("identity_proven"), "identity_proven")
            or False,
            metadata=_metadata(payload.get("metadata")),
            actor=default_actor_resolver(context),
            operation=_operation_context(context, item.project_id),
        )
        return _observation_resource(observation)

    async def add_claim(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        item = research.repository.get_item(resource_ref)
        claim = await research.add_claim(
            resource_ref,
            text=_required_string(payload, "text"),
            category=_required_string(payload, "category"),
            confidence=_enum_optional(
                ClaimConfidence, payload.get("confidence"), ClaimConfidence.UNKNOWN, "confidence"
            ),
            author_ref=_optional_string(payload.get("author_ref"), "author_ref"),
            agent_id=_optional_string(payload.get("agent_id"), "agent_id"),
            agent_revision=_optional_int(payload.get("agent_revision"), "agent_revision"),
            run_id=_optional_string(payload.get("run_id"), "run_id"),
            provenance_metadata=_metadata(payload.get("metadata")),
            actor=default_actor_resolver(context),
            operation=_operation_context(context, item.project_id),
        )
        return _claim_resource(claim)

    async def add_evidence(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        claim = research.repository.get_claim(resource_ref)
        item = research.repository.get_item(claim.research_item_id)
        evidence = await research.add_evidence(
            resource_ref,
            _required_string(payload, "source_observation_id"),
            relation=_enum(EvidenceRelation, payload, "relation"),
            location_ref=_optional_string(payload.get("location_ref"), "location_ref"),
            artifact_id=_optional_string(payload.get("artifact_id"), "artifact_id"),
            excerpt_digest=_optional_string(payload.get("excerpt_digest"), "excerpt_digest"),
            extraction_method=(
                _optional_string(payload.get("extraction_method"), "extraction_method") or "manual"
            ),
            task_id=_optional_string(payload.get("task_id"), "task_id"),
            run_id=_optional_string(payload.get("run_id"), "run_id"),
            agent_id=_optional_string(payload.get("agent_id"), "agent_id"),
            agent_revision=_optional_int(payload.get("agent_revision"), "agent_revision"),
            actor=default_actor_resolver(context),
            operation=_operation_context(context, item.project_id),
        )
        return _evidence_resource(research, evidence.evidence_id)

    async def revalidate_evidence(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        evidence = research.repository.get_evidence(resource_ref)
        item = research.repository.get_item(evidence.research_item_id)
        replacement = await research.revalidate_evidence(
            resource_ref,
            _required_string(payload, "source_observation_id"),
            actor=default_actor_resolver(context),
            operation=_operation_context(context, item.project_id),
        )
        return _evidence_resource(research, replacement.evidence_id)

    for command, handler in (
        ("research.create", create_item),
        ("research.source.add", add_source),
        ("research.source.observe", observe_source),
        ("research.claim.add", add_claim),
        ("research.evidence.add", add_evidence),
        ("research.evidence.revalidate", revalidate_evidence),
    ):
        control_plane.register_command(command, handler)


def _item_resource(research: ResearchService, item: ResearchItem) -> dict[str, JsonValue]:
    freshness: dict[str, JsonValue] = {
        evidence.evidence_id: research.evidence_freshness(evidence.evidence_id).value
        for evidence in research.repository.list_evidence(item.research_item_id)
    }
    bindings = research.repository.list_verification_bindings(item.research_item_id)
    return {
        "id": item.research_item_id,
        "type": "research-item",
        "title": item.title,
        "question": item.question,
        "research_class": item.research_class.value,
        "status": item.status.value,
        "revision": item.revision,
        "digest": item.digest,
        "owner_type": item.owner_ref.type,
        "owner_id": item.owner_ref.id,
        "project_id": item.project_id,
        "workspace_id": item.workspace_id,
        "task_id": item.task_id,
        "plan_id": item.plan_id,
        "run_id": item.run_id,
        "data_class": item.data_class,
        "constraints": list(item.constraints),
        "source_ids": list(item.source_ids),
        "claim_ids": list(item.claim_ids),
        "evidence_ids": list(item.evidence_ids),
        "verification_ids": list(item.verification_ids),
        "verification_bindings": [_binding_resource(value) for value in bindings],
        "evidence_freshness": freshness,
        "freshness_policy": {
            "max_age_seconds": item.freshness_policy.max_age_seconds,
            "revalidate_on_source_change": item.freshness_policy.revalidate_on_source_change,
        },
        "supersedes_research_item_id": item.supersedes_research_item_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "metadata": _safe_metadata(item.metadata),
    }


def _source_resource(research: ResearchService, source: SourceRecord) -> dict[str, JsonValue]:
    current = (
        None
        if source.current_observation_id is None
        else research.repository.get_observation(source.current_observation_id)
    )
    return {
        "id": source.source_id,
        "type": "research-source",
        "research_item_id": source.research_item_id,
        "source_type": source.source_type.value,
        "locator": source.locator,
        "title": source.title,
        "author": source.author,
        "publisher": source.publisher,
        "license_ref": source.license_ref,
        "trust_classification": source.trust_classification,
        "current_observation_id": source.current_observation_id,
        "observation_ids": list(source.observation_ids),
        "current_observation_state": None if current is None else current.state.value,
        "current_binding": None if current is None else _observation_binding(current),
        "created_at": source.created_at.isoformat(),
        "metadata": _safe_metadata(source.metadata),
    }


def _observation_resource(observation: SourceObservation) -> dict[str, JsonValue]:
    return {
        "id": observation.observation_id,
        "type": "research-source-observation",
        "research_item_id": observation.research_item_id,
        "source_id": observation.source_id,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "state": observation.state.value,
        "identity_proven": observation.identity_proven,
        "revision": observation.revision,
        "version": observation.version,
        "commit": observation.commit,
        "etag": observation.etag,
        "content_digest": observation.content_digest,
        "snapshot_digest": observation.snapshot_digest,
        "snapshot_artifact_id": observation.snapshot_artifact_id,
        "repository_id": observation.repository_id,
        "requested_repository_revision": observation.requested_repository_revision,
        "resolved_repository_revision": observation.resolved_repository_revision,
        "intelligence_provider_id": observation.intelligence_provider_id,
        "metadata": _safe_metadata(observation.metadata),
    }


def _claim_resource(claim: object) -> dict[str, JsonValue]:
    from .models import Claim

    assert isinstance(claim, Claim)
    return {
        "id": claim.claim_id,
        "type": "research-claim",
        "research_item_id": claim.research_item_id,
        "revision": claim.revision,
        "digest": claim.digest,
        "text": claim.text,
        "category": claim.category,
        "confidence": claim.confidence.value,
        "status": claim.status.value,
        "evidence_ids": list(claim.evidence_ids),
        "author_ref": claim.author_ref,
        "agent_id": claim.agent_id,
        "agent_revision": claim.agent_revision,
        "run_id": claim.run_id,
        "supersedes_claim_id": claim.supersedes_claim_id,
        "created_at": claim.created_at.isoformat(),
        "metadata": _safe_metadata(claim.metadata),
    }


def _evidence_resource(research: ResearchService, evidence_id: str) -> dict[str, JsonValue]:
    evidence = research.repository.get_evidence(evidence_id)
    return {
        "id": evidence.evidence_id,
        "type": "research-evidence",
        "research_item_id": evidence.research_item_id,
        "source_id": evidence.source_id,
        "source_observation_id": evidence.source_observation_id,
        "claim_id": evidence.claim_id,
        "relation": evidence.relation.value,
        "freshness": research.evidence_freshness(evidence.evidence_id).value,
        "digest": evidence.digest,
        "retrieved_at": evidence.retrieved_at.isoformat(),
        "task_id": evidence.task_id,
        "run_id": evidence.run_id,
        "agent_id": evidence.agent_id,
        "agent_revision": evidence.agent_revision,
        "location_ref": evidence.location_ref,
        "source_revision": evidence.source_revision,
        "source_version": evidence.source_version,
        "source_commit": evidence.source_commit,
        "source_etag": evidence.source_etag,
        "source_content_digest": evidence.source_content_digest,
        "source_snapshot_digest": evidence.source_snapshot_digest,
        "artifact_id": evidence.artifact_id,
        "excerpt_digest": evidence.excerpt_digest,
        "extraction_method": evidence.extraction_method,
        "supersedes_evidence_id": evidence.supersedes_evidence_id,
        "created_at": evidence.created_at.isoformat(),
        "metadata": _safe_metadata(evidence.metadata),
    }


def _binding_resource(binding: object) -> dict[str, JsonValue]:
    from .models import ResearchVerificationBinding

    assert isinstance(binding, ResearchVerificationBinding)
    return {
        "binding_id": binding.binding_id,
        "verification_id": binding.verification_id,
        "subject_type": binding.subject_type.value,
        "subject_id": binding.subject_id,
        "subject_revision": binding.subject_revision,
        "subject_digest": binding.subject_digest,
        "created_at": binding.created_at.isoformat(),
    }


def _observation_binding(observation: SourceObservation) -> dict[str, JsonValue]:
    return {
        "revision": observation.revision,
        "version": observation.version,
        "commit": observation.commit,
        "etag": observation.etag,
        "content_digest": observation.content_digest,
        "snapshot_digest": observation.snapshot_digest,
        "resolved_repository_revision": observation.resolved_repository_revision,
    }


def _visible(item: ResearchItem, context: RequestContext) -> bool:
    if context.actor.owner_type is None or context.actor.owner_id is None:
        return False
    return item.owner_ref.type == context.actor.owner_type and item.owner_ref.id == context.actor.owner_id


def _require_visible(item: ResearchItem, context: RequestContext) -> None:
    if not _visible(item, context):
        raise ContractError(ErrorCode.NOT_FOUND, "Research resource was not found")


def _owner_ref(context: RequestContext) -> OwnerRef:
    if context.actor.owner_type is None or context.actor.owner_id is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "Research creation requires an authenticated owner scope",
        )
    return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)


def _operation_context(context: RequestContext, project_id: str | None) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        owner_type=context.actor.owner_type,
        owner_id=context.actor.owner_id,
        project_id=project_id,
    )


def _required_string(payload: dict[str, JsonValue], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-blank string")
    return value


def _optional_string(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-blank string")
    return value


def _optional_int(value: JsonValue, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an integer")
    return value


def _optional_bool(value: JsonValue, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a boolean")
    return value


def _string_tuple(value: JsonValue, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a string array")
    return tuple(cast(list[str], value))


def _metadata(value: JsonValue) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "metadata must be an object")
    return dict(value)


def _safe_metadata(value: object) -> dict[str, JsonValue]:
    sanitized = redact_sensitive(dict(cast(dict[str, JsonValue], value)))
    if not isinstance(sanitized, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "Research metadata could not be projected")
    return cast(dict[str, JsonValue], sanitized)


def _freshness_policy(value: JsonValue) -> FreshnessPolicy:
    if value is None:
        return FreshnessPolicy()
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "freshness_policy must be an object")
    raw_age = value.get("max_age_seconds")
    if raw_age is not None and (isinstance(raw_age, bool) or not isinstance(raw_age, (int, float))):
        raise ContractError(ErrorCode.INVALID_REQUEST, "max_age_seconds must be numeric or null")
    raw_revalidate = value.get("revalidate_on_source_change", True)
    if not isinstance(raw_revalidate, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "revalidate_on_source_change must be a boolean"
        )
    return FreshnessPolicy(
        max_age_seconds=None if raw_age is None else float(raw_age),
        revalidate_on_source_change=raw_revalidate,
    )


def _datetime(payload: dict[str, JsonValue], field_name: str) -> datetime:
    raw = _required_string(payload, field_name)
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be ISO-8601") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must include a timezone")
    return value


def _enum(enum_type: type[object], payload: dict[str, JsonValue], field_name: str) -> object:
    raw = _required_string(payload, field_name)
    try:
        return enum_type(raw)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"invalid {field_name}: {raw}") from exc


def _enum_optional(
    enum_type: type[object], value: JsonValue, default: object, field_name: str
) -> object:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a string")
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"invalid {field_name}: {value}") from exc


def _require_collection(resource_ref: str, expected: str) -> None:
    if resource_ref != expected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"resource_ref must be {expected!r} for create command",
        )


__all__ = [
    "RESEARCH_CLAIM_COLLECTION",
    "RESEARCH_COLLECTIONS",
    "RESEARCH_COMMANDS",
    "RESEARCH_EVIDENCE_COLLECTION",
    "RESEARCH_ITEM_COLLECTION",
    "RESEARCH_OBSERVATION_COLLECTION",
    "RESEARCH_SOURCE_COLLECTION",
    "ResearchClaimResourceService",
    "ResearchEvidenceResourceService",
    "ResearchItemResourceService",
    "ResearchObservationResourceService",
    "ResearchSourceResourceService",
    "default_actor_resolver",
    "register_research_control_plane",
]
