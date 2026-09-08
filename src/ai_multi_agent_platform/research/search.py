"""Derived global Search integration for canonical Research Evidence (#589)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import RequestContext

from .control_plane import (
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_EVIDENCE_COLLECTION,
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
    ResearchClaimResourceService,
    ResearchEvidenceResourceService,
    ResearchItemResourceService,
    ResearchObservationResourceService,
    ResearchSourceResourceService,
    register_research_control_plane,
)
from .models import Claim, EvidenceRecord, ResearchItem, SourceObservation, SourceRecord
from .service import ResearchService


class ResearchItemSearchResourceService(ResearchItemResourceService):
    """Actor-independent, privacy-minimized Research Item Search enumeration."""

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(_item_search_resource(item) for item in self._research.repository.list_items())

    async def search_result_allowed(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> bool:
        try:
            item = self._research.repository.get_item(resource_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return _visible(item, context)


class ResearchSourceSearchResourceService(ResearchSourceResourceService):
    """Search projection that never indexes canonical locators or arbitrary metadata."""

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        resources: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            for source_id in item.source_ids:
                source = self._research.repository.get_source(source_id)
                resources.append(_source_search_resource(self._research, item, source))
        return tuple(resources)

    async def search_result_allowed(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> bool:
        try:
            source = self._research.repository.get_source(resource_id)
            item = self._research.repository.get_item(source.research_item_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return _visible(item, context)


class ResearchObservationSearchResourceService(ResearchObservationResourceService):
    """Search exact observation identities without indexing source contents or digests."""

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        resources: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            for source_id in item.source_ids:
                source = self._research.repository.get_source(source_id)
                resources.extend(
                    _observation_search_resource(item, source, observation)
                    for observation in self._research.repository.list_observations(source_id)
                )
        return tuple(resources)

    async def search_result_allowed(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> bool:
        try:
            observation = self._research.repository.get_observation(resource_id)
            item = self._research.repository.get_item(observation.research_item_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return _visible(item, context)


class ResearchClaimSearchResourceService(ResearchClaimResourceService):
    """Search Claim text/status while retaining the canonical owner authorization scope."""

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        resources: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            resources.extend(
                _claim_search_resource(item, claim)
                for claim in self._research.repository.list_claims(item.research_item_id)
            )
        return tuple(resources)

    async def search_result_allowed(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> bool:
        try:
            claim = self._research.repository.get_claim(resource_id)
            item = self._research.repository.get_item(claim.research_item_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return _visible(item, context)


class ResearchEvidenceSearchResourceService(ResearchEvidenceResourceService):
    """Search provenance relationships without indexing raw evidence or immutable digests."""

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        resources: list[dict[str, JsonValue]] = []
        for item in self._research.repository.list_items():
            resources.extend(
                _evidence_search_resource(self._research, item, evidence)
                for evidence in self._research.repository.list_evidence(item.research_item_id)
            )
        return tuple(resources)

    async def search_result_allowed(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> bool:
        try:
            evidence = self._research.repository.get_evidence(resource_id)
            item = self._research.repository.get_item(evidence.research_item_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return _visible(item, context)


def research_search_resource_services(
    research: ResearchService,
) -> dict[str, ResourceService]:
    """Return Search-aware replacements for the canonical Research read services."""

    return {
        RESEARCH_ITEM_COLLECTION: ResearchItemSearchResourceService(research),
        RESEARCH_SOURCE_COLLECTION: ResearchSourceSearchResourceService(research),
        RESEARCH_OBSERVATION_COLLECTION: ResearchObservationSearchResourceService(research),
        RESEARCH_CLAIM_COLLECTION: ResearchClaimSearchResourceService(research),
        RESEARCH_EVIDENCE_COLLECTION: ResearchEvidenceSearchResourceService(research),
    }


def register_searchable_research_control_plane(
    control_plane: ControlPlane,
    research: ResearchService,
) -> None:
    """Register canonical Research commands plus Search-aware read projections.

    The Search-aware services replace only the registered ResourceService objects. All
    mutations continue to use the canonical command handlers from
    ``register_research_control_plane`` and therefore retain the existing authorization,
    idempotency and evidence-integrity semantics.
    """

    register_research_control_plane(control_plane, research)
    for collection, service in research_search_resource_services(research).items():
        control_plane.register_resource_service(collection, service)


def _item_search_resource(item: ResearchItem) -> dict[str, JsonValue]:
    return {
        "id": item.research_item_id,
        "type": "research-item",
        "title": item.title,
        "summary": item.question,
        "status": item.status.value,
        "revision": item.revision,
        "owner_type": item.owner_ref.type,
        "owner_id": item.owner_ref.id,
        "project_id": item.project_id,
        "workspace_id": item.workspace_id,
        "task_id": item.task_id,
        "run_id": item.run_id,
        "plan_id": item.plan_id,
        "aliases": [item.research_class.value, item.data_class],
        "updated_at": item.updated_at.isoformat(),
    }


def _source_search_resource(
    research: ResearchService,
    item: ResearchItem,
    source: SourceRecord,
) -> dict[str, JsonValue]:
    current = (
        None
        if source.current_observation_id is None
        else research.repository.get_observation(source.current_observation_id)
    )
    return {
        "id": source.source_id,
        "type": "research-source",
        "title": source.title,
        "summary": f"{source.source_type.value} research source",
        "status": "unobserved" if current is None else current.state.value,
        "owner_type": item.owner_ref.type,
        "owner_id": item.owner_ref.id,
        "project_id": item.project_id,
        "workspace_id": item.workspace_id,
        "task_id": item.task_id,
        "run_id": item.run_id,
        "aliases": [source.source_type.value, source.trust_classification],
        "updated_at": (
            source.created_at.isoformat() if current is None else current.retrieved_at.isoformat()
        ),
    }


def _observation_search_resource(
    item: ResearchItem,
    source: SourceRecord,
    observation: SourceObservation,
) -> dict[str, JsonValue]:
    return {
        "id": observation.observation_id,
        "type": "research-source-observation",
        "title": f"Observation of {source.title}",
        "summary": f"Source observation is {observation.state.value}",
        "status": observation.state.value,
        "owner_type": item.owner_ref.type,
        "owner_id": item.owner_ref.id,
        "project_id": item.project_id,
        "workspace_id": item.workspace_id,
        "task_id": item.task_id,
        "run_id": item.run_id,
        "aliases": [source.source_id, source.source_type.value],
        "updated_at": observation.retrieved_at.isoformat(),
    }


def _claim_search_resource(item: ResearchItem, claim: Claim) -> dict[str, JsonValue]:
    title = claim.text if len(claim.text) <= 160 else f"{claim.text[:157]}..."
    return {
        "id": claim.claim_id,
        "type": "research-claim",
        "title": title,
        "summary": claim.text,
        "status": claim.status.value,
        "revision": claim.revision,
        "owner_type": item.owner_ref.type,
        "owner_id": item.owner_ref.id,
        "project_id": item.project_id,
        "workspace_id": item.workspace_id,
        "task_id": item.task_id,
        "run_id": claim.run_id or item.run_id,
        "aliases": [claim.category, claim.confidence.value],
        "updated_at": claim.created_at.isoformat(),
    }


def _evidence_search_resource(
    research: ResearchService,
    item: ResearchItem,
    evidence: EvidenceRecord,
) -> dict[str, JsonValue]:
    freshness = research.evidence_freshness(evidence.evidence_id).value
    return {
        "id": evidence.evidence_id,
        "type": "research-evidence",
        "title": f"Evidence for claim {evidence.claim_id}",
        "summary": f"{evidence.relation.value} evidence is {freshness}",
        "status": freshness,
        "owner_type": item.owner_ref.type,
        "owner_id": item.owner_ref.id,
        "project_id": item.project_id,
        "workspace_id": item.workspace_id,
        "task_id": evidence.task_id or item.task_id,
        "run_id": evidence.run_id or item.run_id,
        "aliases": [
            evidence.relation.value,
            evidence.claim_id,
            evidence.source_id,
            evidence.source_observation_id,
        ],
        "updated_at": evidence.created_at.isoformat(),
    }


def _visible(item: ResearchItem, context: RequestContext) -> bool:
    if context.actor.owner_type is None or context.actor.owner_id is None:
        return False
    return (
        item.owner_ref.type == context.actor.owner_type
        and item.owner_ref.id == context.actor.owner_id
    )


__all__ = [
    "ResearchClaimSearchResourceService",
    "ResearchEvidenceSearchResourceService",
    "ResearchItemSearchResourceService",
    "ResearchObservationSearchResourceService",
    "ResearchSourceSearchResourceService",
    "register_searchable_research_control_plane",
    "research_search_resource_services",
]
