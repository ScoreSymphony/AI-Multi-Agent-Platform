"""Awaitable Research application service for runtime-critical persistence paths."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.repositories.intelligence.models import (
    RepositoryIntelligenceProvenance,
)
from ai_multi_agent_platform.security import ActorIdentity, AuthorizationAction, AuthorizationGate

from .async_repository import AsyncResearchRepository, AsyncResearchRepositoryAdapter
from .models import (
    Claim,
    ClaimConfidence,
    ClaimStatus,
    EvidenceFreshness,
    EvidenceRecord,
    EvidenceRelation,
    FreshnessPolicy,
    ResearchActionContext,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    ResearchVerificationSubjectType,
    SourceObservation,
    SourceObservationState,
    SourceRecord,
)
from .repository import ResearchRepository
from .service import ResearchService


async def _await_logical_boundary[T](operation: Awaitable[T]) -> T:
    """Defer caller cancellation until one logical Research mutation has settled."""

    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        if task.cancelled():
            raise
        failure = task.exception()
        if failure is not None:
            raise failure from None
        raise


class AsyncResearchService(ResearchService):
    """ResearchService variant whose runtime repository boundary is fully awaitable.

    The inherited synchronous ``repository`` remains available for setup, offline tooling and
    compatibility callers. Supported async runtime compositions use ``runtime_repository``.
    """

    def __init__(
        self,
        repository: ResearchRepository,
        *,
        authorization: AuthorizationGate | None = None,
        runtime_repository: AsyncResearchRepository | None = None,
    ) -> None:
        super().__init__(repository, authorization=authorization)
        self.runtime_repository = runtime_repository or AsyncResearchRepositoryAdapter(repository)

    async def create_item(
        self,
        *,
        title: str,
        question: str,
        research_class: ResearchClass,
        owner_ref: OwnerRef,
        project_id: str | None = None,
        workspace_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        data_class: str = "internal",
        constraints: tuple[str, ...] = (),
        freshness_policy: FreshnessPolicy | None = None,
        provenance: Provenance | None = None,
        metadata: dict[str, JsonValue] | None = None,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> ResearchItem:
        item = ResearchItem(
            title=title,
            question=question,
            research_class=research_class,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            task_id=task_id,
            plan_id=plan_id,
            run_id=run_id,
            data_class=data_class,
            constraints=constraints,
            freshness_policy=freshness_policy or FreshnessPolicy(),
            provenance=provenance,
            metadata=metadata or {},
        )
        await self._authorize(
            actor=actor,
            operation=operation,
            action=AuthorizationAction.CREATE,
            resource_id=item.research_item_id,
            project_id=project_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        return await _await_logical_boundary(self.runtime_repository.create_item(item))

    async def add_source(
        self,
        research_item_id: str,
        *,
        source_type: ResearchSourceType,
        locator: str,
        title: str,
        author: str | None = None,
        publisher: str | None = None,
        license_ref: str | None = None,
        trust_classification: str = "unclassified",
        provenance: Provenance | None = None,
        metadata: dict[str, JsonValue] | None = None,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> SourceRecord:
        item = await self.runtime_repository.get_item(research_item_id)
        await self._authorize_item(item, actor, operation, AuthorizationAction.MODIFY)
        source = SourceRecord(
            research_item_id=research_item_id,
            source_type=source_type,
            locator=locator,
            title=title,
            author=author,
            publisher=publisher,
            license_ref=license_ref,
            trust_classification=trust_classification,
            provenance=provenance,
            metadata=metadata or {},
        )

        async def mutate() -> SourceRecord:
            stored = await self.runtime_repository.create_source(source)
            await self._link_item_runtime(item, source_id=stored.source_id)
            return stored

        return await _await_logical_boundary(mutate())

    async def observe_source(
        self,
        source_id: str,
        *,
        retrieved_at: datetime,
        idempotency_key: str | None = None,
        revision: str | None = None,
        version: str | None = None,
        commit: str | None = None,
        etag: str | None = None,
        content_digest: str | None = None,
        snapshot_digest: str | None = None,
        snapshot_artifact_id: str | None = None,
        identity_proven: bool = False,
        metadata: dict[str, JsonValue] | None = None,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> SourceObservation:
        source = await self.runtime_repository.get_source(source_id)
        item = await self.runtime_repository.get_item(source.research_item_id)
        await self._authorize_item(item, actor, operation, AuthorizationAction.MODIFY)
        state = await self._observation_state_runtime(
            source,
            revision=revision,
            version=version,
            commit=commit,
            etag=etag,
            content_digest=content_digest,
            snapshot_digest=snapshot_digest,
            identity_proven=identity_proven,
        )
        observation = SourceObservation(
            source_id=source_id,
            research_item_id=source.research_item_id,
            retrieved_at=retrieved_at,
            state=state,
            idempotency_key=idempotency_key,
            revision=revision,
            version=version,
            commit=commit,
            etag=etag,
            content_digest=content_digest,
            snapshot_digest=snapshot_digest,
            snapshot_artifact_id=snapshot_artifact_id,
            identity_proven=identity_proven,
            metadata=metadata or {},
        )

        async def mutate() -> SourceObservation:
            stored = await self.runtime_repository.create_observation(observation)
            if stored.observation_id in source.observation_ids:
                return stored
            await self.runtime_repository.save_source(
                replace(
                    source,
                    current_observation_id=stored.observation_id,
                    observation_ids=(*source.observation_ids, stored.observation_id),
                )
            )
            return stored

        return await _await_logical_boundary(mutate())

    async def observe_repository_intelligence(
        self,
        source_id: str,
        provenance: RepositoryIntelligenceProvenance,
        *,
        retrieved_at: datetime,
        content_digest: str | None = None,
        snapshot_digest: str | None = None,
        snapshot_artifact_id: str | None = None,
        idempotency_key: str | None = None,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> SourceObservation:
        source = await self.runtime_repository.get_source(source_id)
        if source.source_type is not ResearchSourceType.REPOSITORY_INTELLIGENCE:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository-intelligence provenance requires a repository-intelligence source",
            )
        item = await self.runtime_repository.get_item(source.research_item_id)
        await self._authorize_item(item, actor, operation, AuthorizationAction.MODIFY)
        previous = await self._current_observation_runtime(source)
        state = SourceObservationState.CURRENT
        if previous is not None and (
            previous.resolved_repository_revision != provenance.resolved_revision
            or previous.content_digest != content_digest
        ):
            state = SourceObservationState.CHANGED
        observation = SourceObservation(
            source_id=source_id,
            research_item_id=source.research_item_id,
            retrieved_at=retrieved_at,
            state=state,
            idempotency_key=idempotency_key,
            commit=provenance.resolved_revision,
            content_digest=content_digest,
            snapshot_digest=snapshot_digest,
            snapshot_artifact_id=snapshot_artifact_id,
            identity_proven=True,
            repository_id=provenance.repository_id,
            requested_repository_revision=provenance.requested_revision,
            resolved_repository_revision=provenance.resolved_revision,
            intelligence_provider_id=provenance.intelligence_provider_id,
            metadata={"freshness": provenance.freshness.value},
        )

        async def mutate() -> SourceObservation:
            stored = await self.runtime_repository.create_observation(observation)
            if stored.observation_id not in source.observation_ids:
                await self.runtime_repository.save_source(
                    replace(
                        source,
                        current_observation_id=stored.observation_id,
                        observation_ids=(*source.observation_ids, stored.observation_id),
                    )
                )
            return stored

        return await _await_logical_boundary(mutate())

    async def add_claim(
        self,
        research_item_id: str,
        *,
        text: str,
        category: str,
        confidence: ClaimConfidence = ClaimConfidence.UNKNOWN,
        author_ref: str | None = None,
        agent_id: str | None = None,
        agent_revision: int | None = None,
        run_id: str | None = None,
        provenance_metadata: dict[str, JsonValue] | None = None,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> Claim:
        item = await self.runtime_repository.get_item(research_item_id)
        await self._authorize_item(item, actor, operation, AuthorizationAction.MODIFY)
        claim = Claim(
            research_item_id=research_item_id,
            text=text,
            category=category,
            confidence=confidence,
            author_ref=author_ref,
            agent_id=agent_id,
            agent_revision=agent_revision,
            run_id=run_id,
            metadata=provenance_metadata or {},
        )

        async def mutate() -> Claim:
            stored = await self.runtime_repository.create_claim(claim)
            await self._link_item_runtime(item, claim_id=stored.claim_id)
            return stored

        return await _await_logical_boundary(mutate())

    async def add_evidence(
        self,
        claim_id: str,
        source_observation_id: str,
        *,
        relation: EvidenceRelation,
        location_ref: str | None = None,
        artifact_id: str | None = None,
        excerpt_digest: str | None = None,
        extraction_method: str = "manual",
        task_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        agent_revision: int | None = None,
        supersedes_evidence_id: str | None = None,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> EvidenceRecord:
        claim = await self.runtime_repository.get_claim(claim_id)
        item = await self.runtime_repository.get_item(claim.research_item_id)
        await self._authorize_item(item, actor, operation, AuthorizationAction.MODIFY)
        observation = await self.runtime_repository.get_observation(source_observation_id)
        if observation.research_item_id != claim.research_item_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Claim and Source Observation belong to different Research Items",
            )
        evidence = EvidenceRecord(
            research_item_id=claim.research_item_id,
            source_id=observation.source_id,
            source_observation_id=observation.observation_id,
            claim_id=claim.claim_id,
            relation=relation,
            retrieved_at=observation.retrieved_at,
            task_id=task_id or item.task_id,
            run_id=run_id,
            agent_id=agent_id,
            agent_revision=agent_revision,
            location_ref=location_ref,
            source_revision=observation.revision,
            source_version=observation.version,
            source_commit=observation.commit,
            source_etag=observation.etag,
            source_content_digest=observation.content_digest,
            source_snapshot_digest=observation.snapshot_digest,
            artifact_id=artifact_id or observation.snapshot_artifact_id,
            excerpt_digest=excerpt_digest,
            extraction_method=extraction_method,
            supersedes_evidence_id=supersedes_evidence_id,
        )

        async def mutate() -> EvidenceRecord:
            stored = await self.runtime_repository.create_evidence(evidence)
            if stored.evidence_id not in claim.evidence_ids:
                await self.runtime_repository.save_claim(
                    replace(
                        claim,
                        evidence_ids=(*claim.evidence_ids, stored.evidence_id),
                        revision=claim.revision + 1,
                    ),
                    expected_revision=claim.revision,
                )
            latest_item = await self.runtime_repository.get_item(item.research_item_id)
            await self._link_item_runtime(latest_item, evidence_id=stored.evidence_id)
            return stored

        return await _await_logical_boundary(mutate())

    async def revalidate_evidence(
        self,
        evidence_id: str,
        new_observation_id: str,
        *,
        actor: ActorIdentity | None = None,
        operation: OperationContext | None = None,
    ) -> EvidenceRecord:
        old = await self.runtime_repository.get_evidence(evidence_id)
        observation = await self.runtime_repository.get_observation(new_observation_id)
        if observation.source_id != old.source_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Evidence revalidation must observe the same canonical Source",
            )
        return await self.add_evidence(
            old.claim_id,
            new_observation_id,
            relation=old.relation,
            location_ref=old.location_ref,
            artifact_id=observation.snapshot_artifact_id,
            excerpt_digest=old.excerpt_digest,
            extraction_method=old.extraction_method,
            task_id=old.task_id,
            run_id=old.run_id,
            agent_id=old.agent_id,
            agent_revision=old.agent_revision,
            supersedes_evidence_id=old.evidence_id,
            actor=actor,
            operation=operation,
        )

    async def evidence_freshness_runtime(
        self,
        evidence_id: str,
        *,
        now: datetime | None = None,
    ) -> EvidenceFreshness:
        evidence = await self.runtime_repository.get_evidence(evidence_id)
        item = await self.runtime_repository.get_item(evidence.research_item_id)
        observation = await self.runtime_repository.get_observation(evidence.source_observation_id)
        source = await self.runtime_repository.get_source(evidence.source_id)
        if observation.state is SourceObservationState.UNAVAILABLE:
            return EvidenceFreshness.UNAVAILABLE
        if observation.state is SourceObservationState.UNVERIFIABLE:
            return EvidenceFreshness.UNVERIFIABLE
        current = await self._current_observation_runtime(source)
        if (
            item.freshness_policy.revalidate_on_source_change
            and current is not None
            and current.observation_id != observation.observation_id
            and current.binding != observation.binding
        ):
            return EvidenceFreshness.STALE
        if item.freshness_policy.max_age_seconds is not None:
            current_time = now or datetime.now(UTC)
            if current_time.tzinfo is None or current_time.utcoffset() is None:
                raise ValueError("freshness evaluation time must be timezone-aware")
            if (
                current_time - observation.retrieved_at
            ).total_seconds() > item.freshness_policy.max_age_seconds:
                return EvidenceFreshness.STALE
        return EvidenceFreshness.CURRENT

    async def assess_claim_runtime(self, claim_id: str, *, now: datetime | None = None) -> Claim:
        claim = await self.runtime_repository.get_claim(claim_id)
        if claim.status in {ClaimStatus.REJECTED, ClaimStatus.SUPERSEDED}:
            return claim
        evidence = [
            await self.runtime_repository.get_evidence(value) for value in claim.evidence_ids
        ]
        current: list[EvidenceRecord] = []
        for value in evidence:
            if (
                await self.evidence_freshness_runtime(value.evidence_id, now=now)
                is EvidenceFreshness.CURRENT
            ):
                current.append(value)
        supports = any(value.relation is EvidenceRelation.SUPPORTS for value in current)
        contradicts = any(value.relation is EvidenceRelation.CONTRADICTS for value in current)
        status = ClaimStatus.PROPOSED
        if supports and contradicts:
            status = ClaimStatus.DISPUTED
        elif supports:
            status = ClaimStatus.SUPPORTED
        elif contradicts:
            status = ClaimStatus.DISPUTED
        if status is claim.status:
            return claim
        return await _await_logical_boundary(
            self.runtime_repository.save_claim(
                replace(claim, status=status, revision=claim.revision + 1),
                expected_revision=claim.revision,
            )
        )

    async def unsupported_claims_runtime(self, research_item_id: str) -> tuple[Claim, ...]:
        async def evaluate() -> tuple[Claim, ...]:
            values: list[Claim] = []
            for claim in await self.runtime_repository.list_claims(research_item_id):
                assessed = await self.assess_claim_runtime(claim.claim_id)
                if assessed.status is not ClaimStatus.SUPPORTED:
                    values.append(assessed)
            return tuple(values)

        return await _await_logical_boundary(evaluate())

    async def record_verification_id_runtime(
        self,
        research_item_id: str,
        verification_id: str,
    ) -> ResearchItem:
        item = await self.runtime_repository.get_item(research_item_id)
        if verification_id in item.verification_ids:
            return item
        return await _await_logical_boundary(
            self.runtime_repository.save_item(
                replace(
                    item,
                    verification_ids=(*item.verification_ids, verification_id),
                    revision=item.revision + 1,
                    updated_at=datetime.now(UTC),
                ),
                expected_revision=item.revision,
            )
        )

    async def build_action_context_runtime(
        self,
        research_item_id: str,
        *,
        require_verification: bool = True,
    ) -> ResearchActionContext:
        async def build() -> ResearchActionContext:
            item = await self.runtime_repository.get_item(research_item_id)
            unsupported = await self.unsupported_claims_runtime(research_item_id)
            if unsupported:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Research contains unsupported, disputed or rejected Claims",
                    details={"claim_ids": [value.claim_id for value in unsupported]},
                )
            claims = tuple(
                value
                for value in await self.runtime_repository.list_claims(research_item_id)
                if value.status is ClaimStatus.SUPPORTED
            )
            if not claims:
                raise ContractError(ErrorCode.CONFLICT, "Research has no supported Claims")
            evidence_values: list[EvidenceRecord] = []
            claim_ids = {claim.claim_id for claim in claims}
            for value in await self.runtime_repository.list_evidence(research_item_id):
                if value.claim_id not in claim_ids or value.relation not in {
                    EvidenceRelation.SUPPORTS,
                    EvidenceRelation.DERIVES_FROM,
                }:
                    continue
                if (
                    await self.evidence_freshness_runtime(value.evidence_id)
                    is EvidenceFreshness.CURRENT
                ):
                    evidence_values.append(value)
            evidence = tuple(evidence_values)
            if not evidence:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Research has no current supporting Evidence",
                )
            verification_ids: tuple[str, ...] = ()
            if require_verification:
                verification_ids = await self._current_verification_ids_runtime(claims, evidence)
            return ResearchActionContext(
                research_item_id=item.research_item_id,
                research_item_revision=item.revision,
                research_item_digest=item.digest,
                claim_ids=tuple(value.claim_id for value in claims),
                evidence_ids=tuple(value.evidence_id for value in evidence),
                verification_ids=verification_ids,
            )

        return await _await_logical_boundary(build())

    async def _current_verification_ids_runtime(
        self,
        claims: tuple[Claim, ...],
        evidence: tuple[EvidenceRecord, ...],
    ) -> tuple[str, ...]:
        item_id = claims[0].research_item_id
        bindings = await self.runtime_repository.list_verification_bindings(item_id)
        required = {
            (
                ResearchVerificationSubjectType.CLAIM,
                claim.claim_id,
                str(claim.revision),
                claim.digest,
            )
            for claim in claims
        }
        required.update(
            (
                ResearchVerificationSubjectType.EVIDENCE,
                value.evidence_id,
                value.source_observation_id,
                value.digest,
            )
            for value in evidence
        )
        matched: dict[tuple[ResearchVerificationSubjectType, str, str, str], str] = {}
        for binding in bindings:
            key = (
                binding.subject_type,
                binding.subject_id,
                binding.subject_revision,
                binding.subject_digest,
            )
            if key in required:
                matched[key] = binding.verification_id
        missing = required - set(matched)
        if missing:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Research action requires current independent Verification",
                details={"missing_subject_count": len(missing)},
            )
        return tuple(sorted(set(matched.values())))

    async def _observation_state_runtime(
        self,
        source: SourceRecord,
        *,
        revision: str | None,
        version: str | None,
        commit: str | None,
        etag: str | None,
        content_digest: str | None,
        snapshot_digest: str | None,
        identity_proven: bool,
    ) -> SourceObservationState:
        if not identity_proven and not any(
            (revision, version, commit, etag, content_digest, snapshot_digest)
        ):
            return SourceObservationState.UNVERIFIABLE
        previous = await self._current_observation_runtime(source)
        if previous is None:
            return SourceObservationState.CURRENT
        next_binding = (
            revision,
            version,
            commit,
            etag,
            content_digest,
            snapshot_digest,
            None,
        )
        if previous.binding != next_binding:
            return SourceObservationState.CHANGED
        return SourceObservationState.CURRENT

    async def _current_observation_runtime(
        self,
        source: SourceRecord,
    ) -> SourceObservation | None:
        if source.current_observation_id is None:
            return None
        return await self.runtime_repository.get_observation(source.current_observation_id)

    async def _link_item_runtime(
        self,
        item: ResearchItem,
        *,
        source_id: str | None = None,
        claim_id: str | None = None,
        evidence_id: str | None = None,
    ) -> ResearchItem:
        source_ids = item.source_ids
        claim_ids = item.claim_ids
        evidence_ids = item.evidence_ids
        if source_id is not None and source_id not in source_ids:
            source_ids = (*source_ids, source_id)
        if claim_id is not None and claim_id not in claim_ids:
            claim_ids = (*claim_ids, claim_id)
        if evidence_id is not None and evidence_id not in evidence_ids:
            evidence_ids = (*evidence_ids, evidence_id)
        if (
            source_ids == item.source_ids
            and claim_ids == item.claim_ids
            and evidence_ids == item.evidence_ids
        ):
            return item
        return await self.runtime_repository.save_item(
            replace(
                item,
                source_ids=source_ids,
                claim_ids=claim_ids,
                evidence_ids=evidence_ids,
                revision=item.revision + 1,
                updated_at=datetime.now(UTC),
            ),
            expected_revision=item.revision,
        )


__all__ = ["AsyncResearchService"]
