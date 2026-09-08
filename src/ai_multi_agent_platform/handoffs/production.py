"""Production composition for canonical Agent Handoffs (#651).

This module bridges the synchronous #592 domain service to the asynchronous platform
boundaries used by production authorization, Artifact/Result resolution and canonical
ContextBundle execution. It deliberately does not introduce new Task/Plan/Step/Run or
messaging authority.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace

from ai_multi_agent_platform.agents import (
    AgentRepository,
    AgentRevisionRef,
    AgentRunRecord,
    AgentTeamRevision,
    AgentTeamRevisionRef,
)
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextAwareOrchestratorAdapter,
    ContextBoundAgentRuntime,
    ContextBudget,
    ContextBundle,
    ContextBundleRepository,
    ContextCandidate,
    ContextRunBinding,
    ContextSourceAdapter,
    ContextSourceRequest,
    ContextSourceType,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    AuthorizationProvider,
    AuthorizationRequest,
    ContractError,
    ErrorCode,
    OperationContext,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
)
from ai_multi_agent_platform.research import ResearchRepository
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    ResourceType,
)
from ai_multi_agent_platform.skills import SkillRepository
from ai_multi_agent_platform.verification import VerificationEvidenceResolver

from .context import handoff_context_candidate
from .coordination import CoordinatedHandoffService
from .models import (
    AgentHandoff,
    HandoffAuditEvent,
    HandoffContent,
    HandoffRuntimeContext,
    HandoffSourceKind,
    HandoffSourceRef,
    ParticipantRef,
    handoff_context_source,
    participant_key,
)
from .repository import HandoffRepository
from .service import (
    ConsumerRequirementEvaluator,
    HandoffAuditSink,
    HandoffReferenceGateway,
    HandoffService,
)

_PRODUCTION_PROVENANCE_SOURCE = "agent-handoff-production-runtime/v1"


@dataclass(frozen=True, slots=True)
class _ResolvedReference:
    revision: str
    digest: str
    project_id: str | None = None
    workspace_id: str | None = None


@dataclass(slots=True)
class _PreparedReads:
    existing: set[tuple[str, str, str | None, str | None]]
    readable: set[
        tuple[
            tuple[str, str, int],
            tuple[str, str, str | None, str | None],
        ]
    ]


class CanonicalHandoffReferenceGateway(HandoffReferenceGateway):
    """Production source gateway backed by canonical owning domains and #15.

    ``HandoffService`` intentionally remains synchronous for the stable #592 contract.
    Production callers therefore prepare exact source checks asynchronously first. The
    resulting authorization evidence is stored in a task-local ``ContextVar`` and the
    synchronous ``exists``/``can_read`` methods fail closed unless the exact reference
    and participant were prepared in the same runtime operation.
    """

    def __init__(
        self,
        *,
        authorization: AuthorizationProvider,
        verification: VerificationEvidenceResolver,
        research: ResearchRepository,
        skills: SkillRepository,
        contexts: ContextBundleRepository,
    ) -> None:
        self.authorization = authorization
        self.verification = verification
        self.research = research
        self.skills = skills
        self.contexts = contexts
        self._prepared: ContextVar[_PreparedReads | None] = ContextVar(
            "handoff_prepared_reads", default=None
        )

    def begin(self) -> Token[_PreparedReads | None]:
        return self._prepared.set(_PreparedReads(existing=set(), readable=set()))

    def reset(self, token: Token[_PreparedReads | None]) -> None:
        self._prepared.reset(token)

    async def prepare_read(
        self,
        participant: ParticipantRef,
        reference: HandoffSourceRef,
        *,
        task_id: str,
        run_id: str | None,
        actor: ActorIdentity,
        operation: OperationContext,
    ) -> None:
        prepared = self._prepared.get()
        if prepared is None:
            raise RuntimeError("handoff reference preparation requires an active operation scope")

        resolved = await self._resolve(reference, task_id=task_id)
        self._require_exact_reference(reference, resolved)
        scoped_operation = _source_operation(operation, resolved)
        decision = normalize_authorization_decision(
            await self.authorization.authorize(
                AuthorizationRequest(
                    principal_ref=actor.actor_id,
                    actor_type=actor.actor_type.value,
                    action=(
                        AuthorizationAction.RESULT_READ.value
                        if reference.kind is HandoffSourceKind.RESULT
                        else AuthorizationAction.READ.value
                    ),
                    resource_type=(
                        ResourceType.ARTIFACT.value
                        if reference.kind is HandoffSourceKind.ARTIFACT
                        else ResourceType.GENERIC.value
                    ),
                    resource_ref=reference.resource_id,
                    context=scoped_operation,
                    organization_id=actor.organization_id,
                    team_id=actor.team_ids[0] if len(actor.team_ids) == 1 else None,
                    workspace_id=resolved.workspace_id,
                    task_id=task_id,
                    run_id=run_id,
                    agent_id=_actor_agent_id(actor),
                    side_effect="handoff_source_read",
                    trust_context={
                        "handoff_reference_kind": reference.kind.value,
                        "handoff_participant": _participant_label(participant),
                    },
                )
            )
        )
        if decision.outcome is not AuthorizationOutcome.ALLOW:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "handoff source reference is not authorized",
                details={
                    "reference_kind": reference.kind.value,
                    "reference_id": reference.resource_id,
                    "authorization_outcome": decision.outcome.value,
                    "policy_id": decision.policy_id,
                },
            )

        prepared.existing.add(reference.identity)
        prepared.readable.add((participant_key(participant), reference.identity))

    def exists(self, reference: HandoffSourceRef) -> bool:
        prepared = self._prepared.get()
        return prepared is not None and reference.identity in prepared.existing

    def can_read(self, participant: ParticipantRef, reference: HandoffSourceRef) -> bool:
        prepared = self._prepared.get()
        return (
            prepared is not None
            and (
                participant_key(participant),
                reference.identity,
            )
            in prepared.readable
        )

    async def _resolve(self, reference: HandoffSourceRef, *, task_id: str) -> _ResolvedReference:
        kind = reference.kind
        if kind in {HandoffSourceKind.ARTIFACT, HandoffSourceKind.RESULT}:
            subject = await self.verification.resolve_subject(
                task_id=task_id,
                subject_type=kind.value,
                subject_id=reference.resource_id,
            )
            return _ResolvedReference(
                revision=subject.revision,
                digest=_digest_value(subject.digest),
            )

        if kind is HandoffSourceKind.RESEARCH_CLAIM:
            claim = self.research.get_claim(reference.resource_id)
            item = self.research.get_item(claim.research_item_id)
            _require_optional_task_scope(item.task_id, task_id, "Research Claim")
            return _ResolvedReference(
                revision=str(claim.revision),
                digest=_digest_value(claim.digest),
                project_id=item.project_id,
                workspace_id=item.workspace_id,
            )

        if kind is HandoffSourceKind.RESEARCH_EVIDENCE:
            evidence = self.research.get_evidence(reference.resource_id)
            item = self.research.get_item(evidence.research_item_id)
            _require_optional_task_scope(
                evidence.task_id or item.task_id,
                task_id,
                "Research Evidence",
            )
            return _ResolvedReference(
                revision="1",
                digest=_digest_value(evidence.digest),
                project_id=item.project_id,
                workspace_id=item.workspace_id,
            )

        if kind is HandoffSourceKind.SKILL_BUNDLE:
            skill_bundle = self.skills.get_bundle(reference.resource_id)
            if skill_bundle.task_id != task_id:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "SkillBundle belongs to a different Task",
                )
            return _ResolvedReference(
                revision="1",
                digest=_digest_value(skill_bundle.digest),
                project_id=skill_bundle.project_id,
                workspace_id=skill_bundle.workspace_id,
            )

        if kind is HandoffSourceKind.CONTEXT_BUNDLE:
            try:
                context_bundle = self.contexts.get(reference.resource_id)
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "ContextBundle referenced by Handoff was not found",
                ) from exc
            if context_bundle.task_id != task_id:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "ContextBundle belongs to a different Task",
                )
            return _ResolvedReference(
                revision="1",
                digest=_digest_value(context_bundle.digest),
            )

        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported Handoff source kind: {kind.value}",
        )

    @staticmethod
    def _require_exact_reference(
        reference: HandoffSourceRef,
        resolved: _ResolvedReference,
    ) -> None:
        if reference.revision is not None and reference.revision != resolved.revision:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "handoff source reference revision is missing or stale",
                details={
                    "reference_kind": reference.kind.value,
                    "reference_id": reference.resource_id,
                    "requested_revision": reference.revision,
                    "resolved_revision": resolved.revision,
                },
            )
        if reference.digest is not None and reference.digest != resolved.digest:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "handoff source reference digest is missing or stale",
                details={
                    "reference_kind": reference.kind.value,
                    "reference_id": reference.resource_id,
                },
            )


class CanonicalConsumerRequirementEvaluator(ConsumerRequirementEvaluator):
    """Evaluate late-bound requirements from canonical Agent/Team revisions only."""

    def __init__(self, agents: AgentRepository) -> None:
        self.agents = agents

    def accepts(self, requirements: tuple[str, ...], consumer: ParticipantRef) -> bool:
        if not requirements:
            return False
        roles, capabilities, policies = self._facts(consumer)
        kind, resource_id, _ = participant_key(consumer)
        for requirement in requirements:
            key, separator, value = requirement.partition(":")
            if not separator or not value:
                return False
            if key == "role" and value not in roles:
                return False
            if key == "capability" and value not in capabilities:
                return False
            if key == "policy" and value not in policies:
                return False
            if key == "agent" and (kind != "agent" or value != resource_id):
                return False
            if key == "team" and (kind != "team" or value != resource_id):
                return False
            if key not in {"role", "capability", "policy", "agent", "team"}:
                return False
        return True

    def _facts(self, consumer: ParticipantRef) -> tuple[set[str], set[str], set[str]]:
        if isinstance(consumer, AgentRevisionRef):
            revision = self.agents.get_agent_revision(consumer.agent_id, consumer.revision)
            profile = revision.profile
            roles = {profile.role}
            capabilities = set(profile.capabilities.allowed) | set(
                profile.capabilities.required_ids
            )
            agent_policies = set(profile.policy_hooks.verification_policy_refs)
            if profile.policy_hooks.authorization_profile_ref is not None:
                agent_policies.add(profile.policy_hooks.authorization_profile_ref)
            return roles, capabilities, agent_policies

        team = self.agents.get_team_revision(consumer.team_id, consumer.revision)
        roles = {member.role for member in team.profile.members}
        capabilities = set(team.profile.shared_capability_ids)
        team_policies: set[str] = set()
        if team.profile.coordination_policy_ref is not None:
            team_policies.add(team.profile.coordination_policy_ref)
        for member in team.profile.members:
            revision = self.agents.get_agent_revision(
                member.agent.agent_id,
                member.agent.revision,
            )
            capabilities.update(revision.profile.capabilities.allowed)
            capabilities.update(revision.profile.capabilities.required_ids)
        return roles, capabilities, team_policies


class TelemetryHandoffAuditSink(HandoffAuditSink):
    """Project Handoff lifecycle/security audit records into canonical telemetry."""

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry

    def record(self, event: HandoffAuditEvent) -> None:
        denied = event.event_type.endswith("denied")
        attributes = dict(event.details)
        attributes.update(
            {
                "handoff_id": event.handoff_id,
                "handoff_revision": event.revision,
            }
        )
        self.telemetry.timeline(
            event_name=event.event_type,
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(
                task_id=event.task_id,
                run_id=event.consuming_run_id,
            ),
            timestamp=event.occurred_at,
            outcome=TelemetryOutcome.FAILED if denied else TelemetryOutcome.SUCCEEDED,
            attributes=attributes,
        )


@dataclass(frozen=True, slots=True)
class DurableConsumedHandoffContextAdapter(ContextSourceAdapter):
    """Restart-safe #590 adapter reconstructed exclusively from Handoff persistence."""

    repository: HandoffRepository
    agents: AgentRepository
    adapter_id: str = "canonical-agent-handoff-durable"

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = []
        for consumption in self.repository.list_consumptions_for_run(request.run_id):
            handoff = self.repository.get_handoff(
                consumption.handoff_id,
                consumption.handoff_revision,
            )
            if handoff.task_id != request.task_id:
                continue
            if request.plan_id is not None and handoff.content.plan_id != request.plan_id:
                continue
            if request.step_id is not None and handoff.content.consumer_step_id != request.step_id:
                continue
            if not self._consumer_matches_request(consumption.consumer, request):
                continue
            runtime = HandoffRuntimeContext(
                handoff=handoff,
                consumption=consumption,
                context_source=handoff_context_source(handoff),
            )
            candidates.append(handoff_context_candidate(runtime))
        return tuple(sorted(candidates, key=lambda item: item.source.canonical_key))

    def _consumer_matches_request(
        self,
        consumer: ParticipantRef,
        request: ContextSourceRequest,
    ) -> bool:
        if isinstance(consumer, AgentRevisionRef):
            return (
                consumer.agent_id == request.agent_id
                and consumer.revision == request.agent_revision
            )
        team = self.agents.get_team_revision(consumer.team_id, consumer.revision)
        return any(
            member.agent.agent_id == request.agent_id
            and member.agent.revision == request.agent_revision
            for member in team.profile.members
        )


@dataclass(frozen=True, slots=True)
class HandoffConsumerExecution:
    runtime_context: HandoffRuntimeContext
    context_bundle: ContextBundle
    agent_run: AgentRunRecord
    context_binding: ContextRunBinding


class ProductionHandoffRuntime:
    """Production path from durable Handoff to ContextBoundAgentRuntime execution."""

    def __init__(
        self,
        *,
        service: HandoffService,
        coordinated: CoordinatedHandoffService,
        repository: HandoffRepository,
        references: CanonicalHandoffReferenceGateway,
        agents: AgentRepository,
        context_assembly: ContextAssemblyService,
        context_runtime: ContextBoundAgentRuntime,
        audit: HandoffAuditSink,
    ) -> None:
        self.service = service
        self.coordinated = coordinated
        self.repository = repository
        self.references = references
        self.agents = agents
        self.context_assembly = context_assembly
        self.context_runtime = context_runtime
        self.audit = audit

    async def create_handoff(
        self,
        content: HandoffContent,
        *,
        idempotency_key: str,
        producer_actor: ActorIdentity,
        operation: OperationContext,
        intended_consumer_actor: ActorIdentity | None = None,
        handoff_id: str | None = None,
        expected_previous_revision: int = 0,
    ) -> AgentHandoff:
        self._require_actor_represents(content.producer, producer_actor)
        self._require_run_participant(content.producer_run_id, content.producer, required=True)
        hardened = replace(
            content,
            provenance=Provenance(
                source=_PRODUCTION_PROVENANCE_SOURCE,
                actor_ref=producer_actor.actor_id,
                details={
                    "correlation_id": operation.correlation_id,
                    "causation_id": operation.causation_id,
                    "producer_run_id": content.producer_run_id,
                },
            ),
        )

        token = self.references.begin()
        try:
            for reference in hardened.source_refs:
                await self.references.prepare_read(
                    hardened.producer,
                    reference,
                    task_id=hardened.task_id,
                    run_id=hardened.producer_run_id,
                    actor=producer_actor,
                    operation=operation,
                )
                if hardened.intended_consumer is not None:
                    consumer_actor = intended_consumer_actor or self._default_actor(
                        hardened.intended_consumer
                    )
                    self._require_actor_represents(hardened.intended_consumer, consumer_actor)
                    await self.references.prepare_read(
                        hardened.intended_consumer,
                        reference,
                        task_id=hardened.task_id,
                        run_id=None,
                        actor=consumer_actor,
                        operation=operation,
                    )
            return self.coordinated.create_handoff(
                hardened,
                idempotency_key=idempotency_key,
                handoff_id=handoff_id,
                expected_previous_revision=expected_previous_revision,
            )
        finally:
            self.references.reset(token)

    async def consume_handoff(
        self,
        handoff_id: str,
        revision: int,
        *,
        consuming_run_id: str,
        consumer: ParticipantRef,
        consumer_actor: ActorIdentity,
        operation: OperationContext,
        context_bundle_ref: HandoffSourceRef | None = None,
    ) -> HandoffRuntimeContext:
        self._require_actor_represents(consumer, consumer_actor)
        handoff = self.service.get_handoff(handoff_id, revision)
        self._require_run_participant(consuming_run_id, consumer, required=False)

        token = self.references.begin()
        try:
            for reference in handoff.content.source_refs:
                await self.references.prepare_read(
                    consumer,
                    reference,
                    task_id=handoff.task_id,
                    run_id=consuming_run_id,
                    actor=consumer_actor,
                    operation=operation,
                )
            if context_bundle_ref is not None:
                await self.references.prepare_read(
                    consumer,
                    context_bundle_ref,
                    task_id=handoff.task_id,
                    run_id=consuming_run_id,
                    actor=consumer_actor,
                    operation=operation,
                )
            return self.coordinated.consume_handoff(
                handoff_id,
                revision,
                consuming_run_id=consuming_run_id,
                consumer=consumer,
                context_bundle_ref=context_bundle_ref,
            )
        except ContractError as exc:
            if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.NOT_FOUND}:
                self.audit.record(
                    HandoffAuditEvent(
                        event_type="handoff.reference_denied",
                        handoff_id=handoff.handoff_id,
                        revision=handoff.revision,
                        task_id=handoff.task_id,
                        consuming_run_id=consuming_run_id,
                        details={"error_code": exc.code.value},
                    )
                )
            raise
        finally:
            self.references.reset(token)

    async def start_consumer(
        self,
        handoff_id: str,
        revision: int,
        *,
        consuming_run_id: str,
        consumer: ParticipantRef,
        consumer_actor: ActorIdentity,
        operation: OperationContext,
        budget: ContextBudget,
        consumer_agent: AgentRevisionRef | None = None,
        candidates: tuple[ContextCandidate, ...] = (),
        workspace_id: str | None = None,
        adapter: ContextAwareOrchestratorAdapter | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
    ) -> HandoffConsumerExecution:
        runtime_context = await self.consume_handoff(
            handoff_id,
            revision,
            consuming_run_id=consuming_run_id,
            consumer=consumer,
            consumer_actor=consumer_actor,
            operation=operation,
        )
        execution_agent, team_revision = self._execution_identity(consumer, consumer_agent)
        handoff = runtime_context.handoff
        durable_adapter = DurableConsumedHandoffContextAdapter(self.repository, self.agents)
        bundle = await self.context_assembly.assemble(
            ContextAssemblyRequest(
                task_id=handoff.task_id,
                run_id=consuming_run_id,
                agent_id=execution_agent.agent_id,
                agent_revision=execution_agent.revision,
                actor=consumer_actor,
                operation=operation,
                candidates=candidates,
                budget=budget,
                workspace_id=workspace_id,
                plan_id=handoff.content.plan_id,
                step_id=handoff.content.consumer_step_id,
            ),
            adapters=(durable_adapter,),
        )
        self._require_bundle_contains_handoff(bundle, runtime_context)
        record, binding = await self.context_runtime.start_agent(
            bundle=bundle,
            adapter=adapter,
            team_revision=team_revision,
            shared_capability_ids=(
                () if team_revision is None else team_revision.profile.shared_capability_ids
            ),
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            verification_context={
                "handoff_id": handoff.handoff_id,
                "handoff_revision": handoff.revision,
                "handoff_digest": handoff.content_digest,
            },
        )
        self._require_agent_run_matches(record, consumer, execution_agent)
        return HandoffConsumerExecution(
            runtime_context=runtime_context,
            context_bundle=bundle,
            agent_run=record,
            context_binding=binding,
        )

    def _require_run_participant(
        self,
        run_id: str,
        participant: ParticipantRef,
        *,
        required: bool,
    ) -> None:
        records = self.agents.list_agent_runs(run_id)
        matching = [record for record in records if _record_matches(record, participant)]
        if matching:
            return
        if records or required:
            raise ContractError(
                ErrorCode.CONFLICT,
                "AgentRun evidence does not match the exact Handoff participant revision",
                details={
                    "run_id": run_id,
                    "participant": _participant_label(participant),
                },
            )

    def _require_actor_represents(
        self,
        participant: ParticipantRef,
        actor: ActorIdentity,
    ) -> None:
        agent_id, actor_revision = _actor_agent_identity(actor)
        if agent_id is None:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Handoff source authorization requires the actual Agent actor",
            )
        if isinstance(participant, AgentRevisionRef):
            if agent_id != participant.agent_id or (
                actor_revision is not None and actor_revision != participant.revision
            ):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "actor does not represent the exact Handoff Agent revision",
                )
            return
        team = self.agents.get_team_revision(participant.team_id, participant.revision)
        if not any(
            member.agent.agent_id == agent_id
            and (actor_revision is None or member.agent.revision == actor_revision)
            for member in team.profile.members
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "actor is not a member of the exact Handoff Team revision",
            )

    def _default_actor(self, participant: ParticipantRef) -> ActorIdentity:
        if isinstance(participant, AgentRevisionRef):
            return ActorIdentity(participant.agent_id, ActorType.AGENT)
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Team Handoff creation requires intended_consumer_actor for source authorization",
        )

    def _execution_identity(
        self,
        consumer: ParticipantRef,
        consumer_agent: AgentRevisionRef | None,
    ) -> tuple[AgentRevisionRef, AgentTeamRevision | None]:
        if isinstance(consumer, AgentRevisionRef):
            if consumer_agent is not None and consumer_agent != consumer:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "consumer_agent differs from direct Handoff consumer",
                )
            return consumer, None
        if consumer_agent is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Team Handoff execution requires an exact member Agent revision",
            )
        team = self.agents.get_team_revision(consumer.team_id, consumer.revision)
        if not any(member.agent == consumer_agent for member in team.profile.members):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "selected consumer Agent is not a member of the exact Team revision",
            )
        return consumer_agent, team

    @staticmethod
    def _require_bundle_contains_handoff(
        bundle: ContextBundle,
        runtime_context: HandoffRuntimeContext,
    ) -> None:
        matches = [
            entry
            for entry in bundle.entries
            if entry.source.source_type is ContextSourceType.AGENT_HANDOFF
            and entry.source.source_id == runtime_context.handoff.handoff_id
            and entry.source.revision == str(runtime_context.handoff.revision)
            and entry.source.digest == runtime_context.handoff.content_digest
        ]
        if len(matches) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "consumer ContextBundle does not contain the exact durable Handoff",
            )

    @staticmethod
    def _require_agent_run_matches(
        record: AgentRunRecord,
        consumer: ParticipantRef,
        execution_agent: AgentRevisionRef,
    ) -> None:
        if record.agent != execution_agent:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "consumer AgentRun does not match the exact selected Agent revision",
            )
        if isinstance(consumer, AgentTeamRevisionRef) and record.team != consumer:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "consumer AgentRun does not retain the exact Handoff Team revision",
            )


def _record_matches(record: AgentRunRecord, participant: ParticipantRef) -> bool:
    if isinstance(participant, AgentRevisionRef):
        return record.agent == participant
    return record.team == participant


def _participant_label(participant: ParticipantRef) -> str:
    kind, resource_id, revision = participant_key(participant)
    return f"{kind}:{resource_id}@{revision}"


def _actor_agent_id(actor: ActorIdentity) -> str | None:
    agent_id, _ = _actor_agent_identity(actor)
    return agent_id


def _actor_agent_identity(actor: ActorIdentity) -> tuple[str | None, int | None]:
    if actor.actor_type is not ActorType.AGENT:
        return None, None
    raw = actor.actor_id
    if raw.startswith("agent_"):
        return raw, None
    if not raw.startswith("agent:"):
        return None, None
    payload = raw.removeprefix("agent:")
    agent_id, separator, revision = payload.partition("@")
    if not agent_id.startswith("agent_"):
        return None, None
    if not separator:
        return agent_id, None
    try:
        return agent_id, int(revision)
    except ValueError:
        return None, None


def _source_operation(
    operation: OperationContext,
    reference: _ResolvedReference,
) -> OperationContext:
    if reference.project_id is None:
        return operation
    if operation.project_id is not None and operation.project_id != reference.project_id:
        raise ContractError(
            ErrorCode.NOT_FOUND,
            "handoff source belongs to a different Project",
        )
    return replace(operation, project_id=reference.project_id)


def _require_optional_task_scope(
    source_task_id: str | None,
    task_id: str,
    kind: str,
) -> None:
    if source_task_id is not None and source_task_id != task_id:
        raise ContractError(ErrorCode.NOT_FOUND, f"{kind} belongs to a different Task")


def _digest_value(value: str) -> str:
    candidate = value.removeprefix("sha256:").lower()
    if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "canonical source returned a non-SHA-256 digest",
        )
    return candidate
