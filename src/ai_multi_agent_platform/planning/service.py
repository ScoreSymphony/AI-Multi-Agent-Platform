"""Platform-owned autonomous planning and bounded replanning service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    OperationContext,
    OperationControl,
    PlatformEvent,
    RetryMode,
)
from ai_multi_agent_platform.domain import Plan, Provenance, RunStatus, Step, TaskStatus
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.models import ModelLocation, ModelRegistry, RoutingRequirements
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    AuthorizationOutcome,
    ProposedAction,
    ResourceType,
    RiskClassification,
)

from .models import (
    PlannerOutput,
    PlanningAgentCandidate,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningModelCandidate,
    PlanningRequest,
    PlanningStepDraft,
    PlanningTeamCandidate,
    PlanningTrigger,
    PlanProposal,
    PriorPlanSnapshot,
    ProposalRecord,
    ProposalStatus,
    ProposalValidation,
    ReplanPolicy,
    new_plan_proposal_id,
)
from .providers import Planner
from .repository import PlanningRepository, advance_record

PlanningEventSink = Callable[[str, dict[str, JsonValue]], Awaitable[None] | None]

_FORBIDDEN_PROVIDER_METADATA_KEYS = frozenset(
    {
        "provider_id",
        "provider_model_id",
        "provider_native_id",
        "native_model_id",
        "host_id",
        "node_id",
        "worker_id",
        "gpu_id",
        "vps_id",
        "endpoint_id",
    }
)


class PlanningKernel(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]: ...

    async def plan_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...


class ActivatedPlanCoordinator(Protocol):
    async def register_plan(self, plan: Plan, steps: tuple[Step, ...]) -> object: ...


class PlanningService:
    """Create, validate and activate immutable Plan proposals without executing Steps.

    When a coordinator is configured, activation hands the exact canonical Plan/Step graph to
    #384. The planning layer never creates Runs, invokes capabilities or dispatches Workers.
    """

    def __init__(
        self,
        *,
        planner: Planner,
        repository: PlanningRepository,
        kernel: PlanningKernel,
        agents: AgentRepository | None = None,
        capabilities: CapabilityRegistry | None = None,
        models: ModelRegistry | None = None,
        authorization: AuthorizationGate | None = None,
        coordinator: ActivatedPlanCoordinator | None = None,
        replan_policy: ReplanPolicy | None = None,
        event_sink: PlanningEventSink | None = None,
    ) -> None:
        self.planner = planner
        self.repository = repository
        self.kernel = kernel
        self.agents = agents
        self.capabilities = capabilities
        self.models = models
        self.authorization = authorization
        self.coordinator = coordinator
        self.replan_policy = replan_policy or ReplanPolicy()
        self._event_sink = event_sink

    async def propose(
        self,
        *,
        task_id: str,
        idempotency_key: str,
        trigger: PlanningTrigger = PlanningTrigger.INITIAL,
        reason: str | None = None,
        workspace_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        task_constraints: tuple[str, ...] = (),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        max_steps: int = 128,
        max_parallel_steps: int | None = None,
    ) -> ProposalRecord:
        """Create one durable proposal for one canonical trigger, idempotently."""

        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "planning idempotency key is required")
        if trigger is not PlanningTrigger.INITIAL and (reason is None or not reason.strip()):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replanning requires a canonical trigger reason",
            )
        existing = self.repository.get_by_idempotency(task_id, idempotency_key)
        if existing is not None:
            return existing

        task = await self.kernel.get_task(task_id)
        prior_plan = await self._prior_plan(task)
        if trigger is PlanningTrigger.INITIAL and prior_plan is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "initial planning cannot replace an existing canonical Plan; use a replan trigger",
            )
        if trigger is not PlanningTrigger.INITIAL and prior_plan is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "replanning requires an existing canonical Plan",
            )
        fingerprint = self._trigger_fingerprint(
            task=task,
            trigger=trigger,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        duplicate_trigger = self.repository.get_by_trigger(task_id, fingerprint)
        if duplicate_trigger is not None:
            return duplicate_trigger
        self._enforce_replan_budget(task_id, trigger)

        inventory = self._inventory(task, workspace_id)
        request = PlanningRequest(
            task_id=task_id,
            task_revision=task.revision,
            objective=task.task.description,
            context=self._operation_context(task, idempotency_key),
            inventory=inventory,
            trigger=trigger,
            reason=reason,
            workspace_id=workspace_id,
            prior_plan=prior_plan,
            evidence_refs=evidence_refs,
            task_constraints=task_constraints,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            max_steps=max_steps,
            max_parallel_steps=max_parallel_steps,
        )
        await self._emit(
            "planning.requested",
            task_id=task_id,
            trigger=trigger.value,
            task_revision=task.revision,
            base_plan_id=task.plan_ref,
        )
        output = await self.planner.propose(request)
        proposal = self._proposal(request, output)
        validation = self.validate(proposal, request)
        status = ProposalStatus.VALIDATED if validation.valid else ProposalStatus.INVALID
        record = ProposalRecord(
            proposal=proposal,
            status=status,
            idempotency_key=idempotency_key,
            validation=validation,
            trigger_fingerprint=fingerprint,
        )
        stored = self.repository.create(record)
        if stored.proposal.proposal_id != proposal.proposal_id:
            return stored
        await self._emit(
            "planning.proposal.created",
            task_id=task_id,
            proposal_id=proposal.proposal_id,
            proposal_digest=proposal.digest,
            plan_revision=proposal.plan_revision,
            planner_id=proposal.planner.planner_id,
            planner_kind=proposal.planner.kind.value,
            model_config_id=proposal.model_config_id,
        )
        await self._emit(
            "planning.validation.completed",
            task_id=task_id,
            proposal_id=proposal.proposal_id,
            valid=validation.valid,
            approval_required=validation.approval_required,
            error_count=len(validation.errors),
            warning_count=len(validation.warnings),
        )
        return stored

    def validate(self, proposal: PlanProposal, request: PlanningRequest) -> ProposalValidation:
        """Validate one proposal structurally and against sanitized canonical inventory."""

        errors: list[str] = []
        warnings: list[str] = []
        approval_required = False
        steps = proposal.steps
        if not steps:
            errors.append("Plan requires at least one Step")
        if len(steps) > request.max_steps:
            errors.append(f"Plan has {len(steps)} Steps but max_steps is {request.max_steps}")

        keys = [step.key for step in steps]
        if len(keys) != len(set(keys)):
            errors.append("Step keys must be unique")
        known_keys = set(keys)
        for step in steps:
            unknown = set(step.depends_on) - known_keys
            if unknown:
                errors.append(
                    f"Step {step.key} references unknown dependencies: {sorted(unknown)!r}"
                )
        if not errors and self._has_cycle(steps):
            errors.append("Step dependency graph contains a cycle")

        if request.max_parallel_steps is not None and not errors:
            peak = self._peak_parallelism(steps)
            if peak > request.max_parallel_steps:
                errors.append(
                    f"Plan requires parallelism {peak} but max_parallel_steps is "
                    f"{request.max_parallel_steps}"
                )

        agents = {(item.agent_id, item.revision): item for item in request.inventory.agents}
        teams = {(item.team_id, item.revision): item for item in request.inventory.teams}
        models = {item.model_config_id: item for item in request.inventory.models}
        capabilities = self._capability_map(request.inventory)
        prior = request.prior_plan

        for step in steps:
            assignment_agent: PlanningAgentCandidate | None = None
            assignment_team: PlanningTeamCandidate | None = None
            if step.assignment is None:
                errors.append(f"Step {step.key} requires an Agent, Agent Team or role assignment")
            elif step.assignment.agent_id is not None:
                revision = step.assignment.agent_revision
                if revision is None:
                    errors.append(f"Step {step.key} Agent assignment is missing its revision")
                else:
                    assignment_agent = agents.get((step.assignment.agent_id, revision))
                    if assignment_agent is None:
                        errors.append(
                            f"Step {step.key} references missing Agent revision "
                            f"{step.assignment.agent_id}@{revision}"
                        )
                    elif not assignment_agent.enabled:
                        errors.append(
                            f"Step {step.key} references disabled Agent {assignment_agent.agent_id}"
                        )
            elif step.assignment.team_id is not None:
                revision = step.assignment.team_revision
                if revision is None:
                    errors.append(f"Step {step.key} Team assignment is missing its revision")
                else:
                    assignment_team = teams.get((step.assignment.team_id, revision))
                    if assignment_team is None:
                        errors.append(
                            f"Step {step.key} references missing Agent Team revision "
                            f"{step.assignment.team_id}@{revision}"
                        )
                    elif not assignment_team.enabled:
                        errors.append(
                            f"Step {step.key} references disabled/incompatible Agent Team "
                            f"{assignment_team.team_id}"
                        )
            elif step.assignment.role_requirement is not None:
                role = step.assignment.role_requirement
                if not any(item.enabled and item.role == role for item in request.inventory.agents):
                    errors.append(f"Step {step.key} has no enabled Agent for role {role!r}")

            if self._contains_provider_private_metadata(step.metadata):
                errors.append(
                    f"Step {step.key} metadata contains provider-private runtime identity"
                )

            required_by_step = {
                requirement.capability_id
                for requirement in step.capability_requirements
                if requirement.required
            }
            if assignment_agent is not None:
                missing_agent_requirements = (
                    set(assignment_agent.required_capability_ids) - required_by_step
                )
                if missing_agent_requirements:
                    errors.append(
                        f"Step {step.key} omits required Agent capabilities: "
                        f"{sorted(missing_agent_requirements)!r}"
                    )

            for requirement in step.capability_requirements:
                candidate = capabilities.get(requirement.capability_id)
                if candidate is None:
                    if requirement.required:
                        errors.append(
                            f"Step {step.key} requires missing capability "
                            f"{requirement.capability_id}"
                        )
                    else:
                        warnings.append(
                            f"Step {step.key} optional capability is missing: "
                            f"{requirement.capability_id}"
                        )
                    continue
                if requirement.required and not candidate.available:
                    errors.append(
                        f"Step {step.key} requires unavailable capability {candidate.capability_id}"
                    )
                if (
                    requirement.exact_version is not None
                    and candidate.version != requirement.exact_version
                ):
                    errors.append(
                        f"Step {step.key} requires {candidate.capability_id}@"
                        f"{requirement.exact_version}, found {candidate.version}"
                    )
                missing_features = set(requirement.required_features) - set(candidate.features)
                if missing_features:
                    errors.append(
                        f"Step {step.key} capability {candidate.capability_id} misses features "
                        f"{sorted(missing_features)!r}"
                    )
                missing_permissions = set(candidate.required_permissions) - set(
                    request.granted_permissions
                )
                if missing_permissions:
                    errors.append(
                        f"Step {step.key} lacks permissions for {candidate.capability_id}: "
                        f"{sorted(missing_permissions)!r}"
                    )
                if (
                    candidate.required_approvals
                    or candidate.safety != "standard"
                    or candidate.side_effects in {"external", "destructive"}
                ):
                    approval_required = True
                    warnings.append(
                        f"Step {step.key} capability {candidate.capability_id} requires activation "
                        "approval"
                    )
                if assignment_agent is not None:
                    if candidate.capability_id in assignment_agent.denied_capability_ids:
                        errors.append(
                            f"Step {step.key} capability {candidate.capability_id} is denied for "
                            f"Agent {assignment_agent.agent_id}"
                        )
                    if (
                        assignment_agent.allowed_capability_ids
                        and candidate.capability_id not in assignment_agent.allowed_capability_ids
                    ):
                        errors.append(
                            f"Step {step.key} capability {candidate.capability_id} is "
                            "outside Agent "
                            f"{assignment_agent.agent_id} allowlist"
                        )
                if assignment_team is not None and assignment_team.shared_capability_ids:
                    if candidate.capability_id not in assignment_team.shared_capability_ids:
                        warnings.append(
                            f"Step {step.key} capability {candidate.capability_id} is not a shared "
                            "Team capability; member-level policy must provide it"
                        )

            if step.requires_model or self._has_model_requirements(step.model_requirements):
                compatible = [
                    candidate
                    for candidate in request.inventory.models
                    if self._model_matches(candidate, step.model_requirements)
                ]
                if not compatible:
                    errors.append(f"Step {step.key} has no compatible available canonical model")
                explicit = step.model_requirements.explicit_model_id
                if explicit is not None and explicit not in models:
                    errors.append(
                        f"Step {step.key} references unknown canonical model "
                        f"configuration {explicit}"
                    )

            if step.reuse_step_ids:
                if prior is None:
                    errors.append(f"Step {step.key} cannot reuse work without a prior Plan")
                else:
                    allowed_reuse = set(prior.completed_step_ids)
                    invalid_reuse = set(step.reuse_step_ids) - allowed_reuse
                    if invalid_reuse:
                        errors.append(
                            f"Step {step.key} may reuse only completed prior Steps, not "
                            f"{sorted(invalid_reuse)!r}"
                        )

        return ProposalValidation(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(dict.fromkeys(warnings)),
            approval_required=approval_required,
        )

    async def activate(
        self,
        proposal_id: str,
        *,
        idempotency_key: str,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> ProposalRecord:
        """Activate exactly one validated proposal and hand it to #384 when configured."""

        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "activation idempotency key is required")
        record = self.repository.get(proposal_id)
        proposal = record.proposal
        activated_event = await self._activated_plan_event(proposal)
        if activated_event is not None:
            plan_id = self._plan_ref(activated_event)
            await self._handoff_to_coordinator(proposal, activated_event)
            if record.status is ProposalStatus.ACTIVATED and record.activation_plan_id == plan_id:
                return record
            saved = advance_record(
                record,
                status=ProposalStatus.ACTIVATED,
                activation_plan_id=plan_id,
            )
            return self.repository.save(saved, expected_revision=record.revision)

        if record.status is ProposalStatus.INVALID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "invalid planning proposal cannot be activated",
                details={"proposal_id": proposal_id, "errors": list(record.validation.errors)},
            )
        if record.status in {ProposalStatus.REJECTED, ProposalStatus.SUPERSEDED}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"proposal {proposal_id} is {record.status.value}",
            )
        task = await self.kernel.get_task(proposal.task_id)
        if task.revision != proposal.task_revision or task.plan_ref != proposal.base_plan_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "planning proposal is stale against canonical Task/Plan state",
                details={
                    "proposal_id": proposal_id,
                    "proposal_task_revision": proposal.task_revision,
                    "current_task_revision": task.revision,
                    "proposal_base_plan_id": proposal.base_plan_id,
                    "current_plan_id": task.plan_ref,
                },
            )
        if self.coordinator is not None and task.status not in {
            TaskStatus.READY,
            TaskStatus.RUNNING,
        }:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Plan activation with durable execution handoff requires Task ready/running state",
                details={"task_id": proposal.task_id, "task_status": task.status.value},
            )
        if proposal.base_plan_id is not None:
            prior = await self._prior_plan(task)
            if prior is not None and prior.running_step_ids:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "cannot activate a replacement Plan while prior Steps are running",
                    details={"running_step_ids": list(prior.running_step_ids)},
                )

        resolved_actor = actor or ActorIdentity(
            actor_id=f"{task.task.owner_ref.type}:{task.task.owner_ref.id}",
            actor_type=ActorType.SERVICE,
        )
        action = self._activation_action(task, record, resolved_actor)
        if record.validation.approval_required:
            if self.authorization is None:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "planning proposal introduces approval-gated capabilities but no approval "
                    "authority is configured",
                    details={"proposal_id": proposal_id},
                )
            if approval_id is None or not self.authorization.approvals.valid_for(
                approval_id, action
            ):
                pending = await self.authorization.ensure_pending_approval_with_event(
                    action,
                    reason="planning proposal introduces approval-gated capability requirements",
                    policy_id="planning:capability-requirements",
                    risk=RiskClassification.ELEVATED,
                )
                if (
                    record.status is not ProposalStatus.AWAITING_APPROVAL
                    or record.approval_id != pending.approval_id
                ):
                    updated = advance_record(
                        record,
                        status=ProposalStatus.AWAITING_APPROVAL,
                        approval_id=pending.approval_id,
                    )
                    self.repository.save(updated, expected_revision=record.revision)
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "planning proposal requires approval before activation",
                    details={
                        "proposal_id": proposal_id,
                        "approval_id": pending.approval_id,
                        "proposal_digest": proposal.digest,
                    },
                )

        if self.authorization is not None:
            decision = await self.authorization.decide(action, approval_id=approval_id)
            if decision.outcome is not AuthorizationOutcome.ALLOW:
                approval_ref = decision.constraints.get("approval_id")
                if decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
                    approval_value = approval_ref if isinstance(approval_ref, str) else None
                    if record.status is not ProposalStatus.AWAITING_APPROVAL:
                        updated = advance_record(
                            record,
                            status=ProposalStatus.AWAITING_APPROVAL,
                            approval_id=approval_value,
                        )
                        self.repository.save(updated, expected_revision=record.revision)
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    decision.reason or "planning proposal activation is not authorized",
                    details={
                        "proposal_id": proposal_id,
                        "authorization_outcome": decision.outcome.value,
                        "approval_id": approval_ref,
                    },
                )

        if record.status is not ProposalStatus.ACTIVATING:
            activating = advance_record(record, status=ProposalStatus.ACTIVATING)
            record = self.repository.save(activating, expected_revision=record.revision)
        await self._emit(
            "planning.activation.started",
            task_id=proposal.task_id,
            proposal_id=proposal_id,
            plan_revision=proposal.plan_revision,
            base_plan_id=proposal.base_plan_id,
        )
        result = await self.kernel.plan_task(
            idempotency_key=f"planning:{proposal_id}:activate",
            task_id=proposal.task_id,
            actor_ref=resolved_actor.actor_id,
            source="platform-planning",
        )
        if result.plan_ref is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "kernel activated planning proposal without a canonical Plan reference",
            )
        activated_event = await self._activated_plan_event(proposal)
        if activated_event is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "activated proposal is missing its canonical plan.created provenance event",
            )
        await self._handoff_to_coordinator(proposal, activated_event)
        activated = advance_record(
            record,
            status=ProposalStatus.ACTIVATED,
            activation_plan_id=result.plan_ref,
        )
        saved = self.repository.save(activated, expected_revision=record.revision)
        reused_step_ids: list[JsonValue] = [
            step_id
            for step_id in sorted(
                {step_id for step in proposal.steps for step_id in step.reuse_step_ids}
            )
        ]
        await self._emit(
            "planning.revision.activated",
            task_id=proposal.task_id,
            proposal_id=proposal_id,
            plan_id=result.plan_ref,
            plan_revision=proposal.plan_revision,
            base_plan_id=proposal.base_plan_id,
            reused_step_ids=reused_step_ids,
        )
        return saved

    async def reject(self, proposal_id: str, *, reason: str | None = None) -> ProposalRecord:
        record = self.repository.get(proposal_id)
        if record.status is ProposalStatus.ACTIVATED:
            raise ContractError(ErrorCode.CONFLICT, "activated proposal cannot be rejected")
        if record.status is ProposalStatus.REJECTED:
            return record
        rejected = advance_record(
            record,
            status=ProposalStatus.REJECTED,
            failure_reason=reason or "rejected by authorized operator",
        )
        saved = self.repository.save(rejected, expected_revision=record.revision)
        await self._emit(
            "planning.proposal.rejected",
            task_id=record.proposal.task_id,
            proposal_id=proposal_id,
        )
        return saved

    def history(self, task_id: str) -> tuple[ProposalRecord, ...]:
        return self.repository.list_for_task(task_id)

    async def _prior_plan(self, task: TaskState) -> PriorPlanSnapshot | None:
        if task.plan_ref is None:
            return None
        completed: list[str] = []
        running: list[str] = []
        failed: list[str] = []
        not_started: list[str] = []
        for step_id in task.step_ids:
            runs: list[RunState] = []
            for run_id in task.run_ids:
                run = await self.kernel.get_run(task.task_id, run_id)
                if run.run.subject_type == "step" and run.run.subject_id == step_id:
                    runs.append(run)
            if not runs:
                not_started.append(step_id)
                continue
            latest = max(runs, key=lambda item: (item.attempt, item.run.created_at))
            if latest.status is RunStatus.SUCCEEDED:
                completed.append(step_id)
            elif latest.status in {RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING}:
                running.append(step_id)
            else:
                failed.append(step_id)
        history = await self.kernel.history(task.task_id)
        revision = sum(event.event_type == "plan.created" for event in history)
        return PriorPlanSnapshot(
            plan_id=task.plan_ref,
            revision=max(revision, 1),
            completed_step_ids=tuple(completed),
            running_step_ids=tuple(running),
            failed_step_ids=tuple(failed),
            not_started_step_ids=tuple(not_started),
            result_refs=task.result_ids,
            artifact_refs=task.artifact_ids,
        )

    def _inventory(self, task: TaskState, workspace_id: str | None) -> PlanningInventory:
        agent_candidates: list[PlanningAgentCandidate] = []
        team_candidates: list[PlanningTeamCandidate] = []
        capability_candidates: list[PlanningCapabilityCandidate] = []
        model_candidates: list[PlanningModelCandidate] = []

        if self.agents is not None:
            for agent_definition in self.agents.list_agents():
                agent_revision = self.agents.get_agent_revision(
                    agent_definition.agent_id,
                    agent_definition.current_revision,
                )
                if not self._scope_compatible(
                    agent_definition.project_id,
                    agent_definition.workspace_id,
                    task.task.project_id,
                    workspace_id,
                ):
                    continue
                agent_profile = agent_revision.profile
                agent_candidates.append(
                    PlanningAgentCandidate(
                        agent_id=agent_definition.agent_id,
                        revision=agent_revision.revision,
                        role=agent_profile.role,
                        enabled=agent_profile.enabled,
                        project_id=agent_definition.project_id,
                        workspace_id=agent_definition.workspace_id,
                        allowed_capability_ids=agent_profile.capabilities.allowed,
                        denied_capability_ids=agent_profile.capabilities.denied,
                        required_capability_ids=agent_profile.capabilities.required_ids,
                        model_requirements=agent_profile.model.requirements,
                    )
                )
            for team_definition in self.agents.list_teams():
                team_revision = self.agents.get_team_revision(
                    team_definition.team_id, team_definition.current_revision
                )
                if not self._scope_compatible(
                    team_definition.project_id,
                    team_definition.workspace_id,
                    task.task.project_id,
                    workspace_id,
                ):
                    continue
                team_profile = team_revision.profile
                member_enabled = True
                for member in team_profile.members:
                    try:
                        member_revision = self.agents.get_agent_revision(
                            member.agent.agent_id,
                            member.agent.revision,
                        )
                    except ContractError:
                        member_enabled = False
                        break
                    if not member_revision.profile.enabled:
                        member_enabled = False
                        break
                team_candidates.append(
                    PlanningTeamCandidate(
                        team_id=team_definition.team_id,
                        revision=team_revision.revision,
                        enabled=team_profile.enabled and member_enabled,
                        member_agent_ids=tuple(
                            member.agent.agent_id for member in team_profile.members
                        ),
                        project_id=team_definition.project_id,
                        workspace_id=team_definition.workspace_id,
                        shared_capability_ids=team_profile.shared_capability_ids,
                        max_parallel_agents=team_profile.max_parallel_agents,
                        max_steps=team_profile.max_steps,
                    )
                )

        if self.capabilities is not None:
            for spec in self.capabilities.inventory_capabilities(include_unavailable=True):
                capability_candidates.append(
                    PlanningCapabilityCandidate(
                        capability_id=spec.capability_id,
                        version=spec.version,
                        available=spec.available and spec.health is not HealthStatus.UNAVAILABLE,
                        features=spec.features,
                        required_permissions=spec.required_permissions,
                        required_approvals=spec.required_approvals,
                        safety=spec.safety.value,
                        side_effects=spec.side_effects.value,
                    )
                )

        if self.models is not None:
            for config in self.models.list_models():
                health = self.models.effective_health(config)
                caps = config.capabilities
                model_candidates.append(
                    PlanningModelCandidate(
                        model_config_id=config.config_id,
                        enabled=config.enabled,
                        available=(
                            config.enabled
                            and health in {HealthStatus.HEALTHY, HealthStatus.DEGRADED}
                        ),
                        location=config.location,
                        context_window=caps.context_window,
                        tool_calling=caps.tool_calling,
                        structured_output=caps.structured_output,
                        streaming=caps.streaming,
                        modalities=caps.modalities,
                        reasoning=caps.reasoning,
                    )
                )

        return PlanningInventory(
            agents=tuple(agent_candidates),
            teams=tuple(team_candidates),
            capabilities=tuple(capability_candidates),
            models=tuple(model_candidates),
        )

    def _proposal(self, request: PlanningRequest, output: PlannerOutput) -> PlanProposal:
        base = request.prior_plan
        return PlanProposal(
            proposal_id=new_plan_proposal_id(),
            task_id=request.task_id,
            task_revision=request.task_revision,
            plan_revision=1 if base is None else base.revision + 1,
            base_plan_id=None if base is None else base.plan_id,
            trigger=request.trigger,
            reason=request.reason,
            summary=output.draft.summary,
            steps=output.draft.steps,
            assumptions=output.draft.assumptions,
            constraints=tuple(
                dict.fromkeys((*request.task_constraints, *output.draft.constraints))
            ),
            evidence_refs=request.evidence_refs,
            planner=output.planner,
            model_config_id=output.model_config_id,
        )

    async def _activated_plan_event(self, proposal: PlanProposal) -> PlatformEvent | None:
        history = await self.kernel.history(proposal.task_id)
        for event in reversed(history):
            if event.event_type != "plan.created":
                continue
            raw_metadata = event.payload.get("adapter_metadata")
            if not isinstance(raw_metadata, Mapping):
                continue
            planning_metadata = raw_metadata.get("platform-planning")
            if not isinstance(planning_metadata, Mapping):
                continue
            if planning_metadata.get("proposal_id") == proposal.proposal_id:
                return event
        return None

    @staticmethod
    def _plan_ref(event: PlatformEvent) -> str:
        value = event.payload.get("plan_ref")
        if not isinstance(value, str) or not value.strip():
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planning plan.created event is missing canonical plan_ref",
            )
        return value

    async def _handoff_to_coordinator(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
    ) -> None:
        if self.coordinator is None:
            return
        plan_id = self._plan_ref(event)
        raw_steps = event.payload.get("steps")
        if not isinstance(raw_steps, list):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planning plan.created event is missing canonical Step payloads",
            )
        provenance = event.provenance or Provenance(source="platform-planning")
        task = await self.kernel.get_task(proposal.task_id)
        plan = Plan(
            id=plan_id,
            task_id=proposal.task_id,
            owner_ref=task.task.owner_ref,
            revision=proposal.plan_revision,
            active=True,
            project_id=task.task.project_id,
            created_at=event.occurred_at,
            provenance=provenance,
        )
        steps: list[Step] = []
        for raw in raw_steps:
            if not isinstance(raw, dict):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "planning plan.created Step payload must be an object",
                )
            step_id = raw.get("id")
            title = raw.get("title")
            depends_on = raw.get("depends_on", [])
            if (
                not isinstance(step_id, str)
                or not isinstance(title, str)
                or not isinstance(depends_on, list)
                or any(not isinstance(item, str) for item in depends_on)
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "planning plan.created contains malformed canonical Step payload",
                )
            steps.append(
                Step(
                    id=step_id,
                    plan_id=plan_id,
                    title=title,
                    owner_ref=task.task.owner_ref,
                    depends_on=tuple(depends_on),
                    project_id=task.task.project_id,
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                    provenance=provenance,
                )
            )
        await self.coordinator.register_plan(plan, tuple(steps))
        await self._emit(
            "planning.coordination.handoff",
            task_id=proposal.task_id,
            proposal_id=proposal.proposal_id,
            plan_id=plan_id,
            plan_revision=proposal.plan_revision,
            step_count=len(steps),
        )

    def _enforce_replan_budget(self, task_id: str, trigger: PlanningTrigger) -> None:
        if trigger is PlanningTrigger.INITIAL:
            return
        used = sum(
            record.proposal.trigger is not PlanningTrigger.INITIAL
            and record.status is not ProposalStatus.REJECTED
            for record in self.repository.list_for_task(task_id)
        )
        if used >= self.replan_policy.max_replans:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "bounded replanning budget exhausted",
                details={
                    "task_id": task_id,
                    "max_replans": self.replan_policy.max_replans,
                    "used_replans": used,
                },
            )

    def _activation_action(
        self,
        task: TaskState,
        record: ProposalRecord,
        actor: ActorIdentity,
    ) -> ProposedAction:
        proposal = record.proposal
        return ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.TASK,
                resource_id=proposal.task_id,
                operation=self._operation_context(
                    task,
                    f"planning:{proposal.proposal_id}:authorize",
                ),
                task_id=proposal.task_id,
                side_effect="plan_revision_activation",
            ),
            payload={
                "proposal_id": proposal.proposal_id,
                "proposal_digest": proposal.digest,
                "plan_revision": proposal.plan_revision,
                "base_plan_id": proposal.base_plan_id,
                "trigger": proposal.trigger.value,
            },
        )

    def _operation_context(self, task: TaskState, key: str) -> OperationContext:
        return OperationContext(
            correlation_id=task.task_id,
            causation_id=key,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
            control=OperationControl(idempotency_key=key, retry_mode=RetryMode.IDEMPOTENT),
        )

    @staticmethod
    def _scope_compatible(
        candidate_project_id: str | None,
        candidate_workspace_id: str | None,
        task_project_id: str | None,
        workspace_id: str | None,
    ) -> bool:
        if candidate_project_id is not None and candidate_project_id != task_project_id:
            return False
        if candidate_workspace_id is not None and candidate_workspace_id != workspace_id:
            return False
        return True

    @staticmethod
    def _trigger_fingerprint(
        *,
        task: TaskState,
        trigger: PlanningTrigger,
        reason: str | None,
        evidence_refs: tuple[str, ...],
    ) -> str:
        payload = {
            "task_id": task.task_id,
            "task_revision": task.revision,
            "plan_id": task.plan_ref,
            "trigger": trigger.value,
            "reason": reason,
            "evidence_refs": sorted(evidence_refs),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _has_model_requirements(requirements: RoutingRequirements) -> bool:
        return any(
            (
                requirements.explicit_model_id is not None,
                requirements.min_context_window is not None,
                requirements.tool_calling,
                requirements.structured_output,
                requirements.streaming,
                bool(requirements.modalities),
                bool(requirements.reasoning),
                requirements.local_only,
                requirements.self_hosted_only,
            )
        )

    @staticmethod
    def _model_matches(
        candidate: PlanningModelCandidate, requirements: RoutingRequirements
    ) -> bool:
        if not candidate.enabled or not candidate.available:
            return False
        if (
            requirements.explicit_model_id is not None
            and candidate.model_config_id != requirements.explicit_model_id
        ):
            return False
        if requirements.local_only and candidate.location is not ModelLocation.LOCAL:
            return False
        if requirements.self_hosted_only and candidate.location not in {
            ModelLocation.LOCAL,
            ModelLocation.SELF_HOSTED,
        }:
            return False
        if requirements.min_context_window is not None:
            if (
                candidate.context_window is None
                or candidate.context_window < requirements.min_context_window
            ):
                return False
        if requirements.tool_calling and not candidate.tool_calling:
            return False
        if requirements.structured_output and not candidate.structured_output:
            return False
        if requirements.streaming and not candidate.streaming:
            return False
        if requirements.modalities and not set(requirements.modalities).issubset(
            candidate.modalities
        ):
            return False
        if requirements.reasoning and not set(requirements.reasoning).issubset(candidate.reasoning):
            return False
        return True

    @staticmethod
    def _has_cycle(steps: tuple[PlanningStepDraft, ...]) -> bool:
        dependencies = {step.key: step.depends_on for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> bool:
            if key in visited:
                return False
            if key in visiting:
                return True
            visiting.add(key)
            for dependency in dependencies.get(key, ()):
                if visit(dependency):
                    return True
            visiting.remove(key)
            visited.add(key)
            return False

        return any(visit(key) for key in dependencies)

    @staticmethod
    def _peak_parallelism(steps: tuple[PlanningStepDraft, ...]) -> int:
        dependencies = {step.key: set(step.depends_on) for step in steps}
        remaining = dict(dependencies)
        peak = 0
        completed: set[str] = set()
        while remaining:
            ready = sorted(key for key, deps in remaining.items() if deps.issubset(completed))
            if not ready:
                return len(remaining)
            peak = max(peak, len(ready))
            completed.update(ready)
            for key in ready:
                remaining.pop(key)
        return peak

    @staticmethod
    def _contains_provider_private_metadata(metadata: object) -> bool:
        if not isinstance(metadata, Mapping):
            return True
        stack: list[object] = [metadata]
        while stack:
            value = stack.pop()
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if str(key).lower() in _FORBIDDEN_PROVIDER_METADATA_KEYS:
                        return True
                    stack.append(item)
            elif isinstance(value, (list, tuple)):
                stack.extend(value)
        return False

    @staticmethod
    def _capability_map(inventory: PlanningInventory) -> dict[str, PlanningCapabilityCandidate]:
        result: dict[str, PlanningCapabilityCandidate] = {}
        for candidate in inventory.capabilities:
            current = result.get(candidate.capability_id)
            if current is None or (not current.available and candidate.available):
                result[candidate.capability_id] = candidate
        return result

    async def _emit(self, event_type: str, **attributes: JsonValue) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(event_type, dict(attributes))
        if result is not None:
            await result
