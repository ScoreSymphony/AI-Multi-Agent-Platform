"""Reviewer-Agent bridge between canonical Verification and the normal Agent runtime (#86)."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.agents import (
    AgentOrchestratorMapper,
    AgentRunRecord,
    AgentRunStatus,
    AgentRuntime,
)
from ai_multi_agent_platform.capabilities import SideEffectClassification
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import RoutingRequirements

from .evidence import VerificationEvidenceResolver
from .models import (
    VerificationFinding,
    VerificationOutcome,
    VerificationRequest,
    VerificationResult,
    VerifierIdentity,
    VerifierKind,
)
from .service import VerificationService

_REVIEW_CONTEXT_SCHEMA = "verification-reviewer-agent-v1"
_RESERVED_TASK_CONTEXT_KEY = "verification"


class ReviewerAgentRuntime:
    """Run an Agent as a reviewer without granting it Verification/lifecycle authority.

    The bridge prepares the ordinary Agent through ``AgentRuntime``, proves the selected
    revision/model/provider and (where requested by policy) read-only capability set before
    execution begins, and binds the resulting ``AgentRunRecord`` to one exact Verification.
    The Agent/its orchestrator never receives a method that completes the Task. Its output
    becomes only a ``VerificationResult`` submitted back to the platform-owned service.
    """

    def __init__(
        self,
        verification: VerificationService,
        agents: AgentRuntime,
        evidence: VerificationEvidenceResolver | None = None,
    ) -> None:
        self._verification = verification
        self._agents = agents
        self._evidence = evidence

    async def start_review(
        self,
        verification_id: str,
        *,
        run_id: str,
        agent_id: str,
        revision: int | None = None,
        mapper: AgentOrchestratorMapper | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentRunRecord:
        request = self._verification.get_request(verification_id)
        if request.requested_verifier_kind is not VerifierKind.AGENT:
            raise ContractError(
                ErrorCode.CONFLICT,
                "verification request is not assigned to a reviewer Agent",
            )
        if self._evidence is not None:
            canonical_subject = await self._evidence.resolve_subject(
                task_id=request.task_id,
                subject_type=request.subject.subject_type,
                subject_id=request.subject.subject_id,
            )
            if canonical_subject != request.subject:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "reviewer Agent request subject differs from canonical evidence",
                )
        if task_context is not None and _RESERVED_TASK_CONTEXT_KEY in task_context:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "reviewer Agent task_context cannot override canonical verification context",
            )

        policy = self._verification.get_policy(request.policy_id, request.policy_version)
        stage = policy.stage(request.stage_id)
        capability_ids = list(requested_capability_ids)
        if stage.capability_ref is not None and stage.capability_ref not in capability_ids:
            capability_ids.append(stage.capability_ref)
        requested = tuple(capability_ids)

        spec = self._agents.prepare_agent(
            task_id=request.task_id,
            run_id=run_id,
            agent_id=agent_id,
            revision=revision,
            task_model_override=task_model_override,
            requested_capability_ids=requested,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=task_context,
            project_context=project_context,
        )
        read_only = self._spec_is_read_only(spec.capability_ids, spec.capability_versions)
        verifier = VerifierIdentity(
            verifier_ref=_agent_verifier_ref(
                spec.agent_revision.agent_id, spec.agent_revision.revision
            ),
            kind=VerifierKind.AGENT,
            agent_id=spec.agent_revision.agent_id,
            agent_revision=spec.agent_revision.revision,
            model_config_id=spec.selected_model_config_id,
            provider_id=spec.selected_provider_id,
            read_only=read_only,
        )
        self._verification.validate_verifier(request.verification_id, verifier)
        verification_context = _review_context(
            request, verifier, spec.capability_ids, spec.capability_versions
        )
        merged_task_context = dict(task_context or {})
        merged_task_context[_RESERVED_TASK_CONTEXT_KEY] = dict(verification_context)

        record = await self._agents.start_agent(
            task_id=request.task_id,
            run_id=run_id,
            agent_id=spec.agent_revision.agent_id,
            revision=spec.agent_revision.revision,
            mapper=mapper,
            task_model_override=task_model_override,
            requested_capability_ids=requested,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=merged_task_context,
            project_context=project_context,
            verification_context=verification_context,
        )
        actual = _verifier_from_record(record, read_only=read_only)
        if (
            actual != verifier
            or record.capability_ids != spec.capability_ids
            or dict(record.capability_versions) != dict(spec.capability_versions)
        ):
            self._agents.finish_agent_run(
                record.agent_run_id,
                status=AgentRunStatus.FAILED,
                error="reviewer Agent runtime changed a prevalidated execution identity",
            )
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "reviewer Agent execution identity changed after verification preflight",
            )
        return record

    def complete_review(
        self,
        agent_run_id: str,
        *,
        outcome: VerificationOutcome,
        findings: tuple[VerificationFinding, ...] = (),
        evidence_artifact_ids: tuple[str, ...] = (),
        checks_executed: tuple[str, ...] = ("agent_review",),
        output_artifact_ids: tuple[str, ...] = (),
        output_result_ids: tuple[str, ...] = (),
        model_call_refs: tuple[str, ...] = (),
        tool_invocation_refs: tuple[str, ...] = (),
        telemetry: Mapping[str, JsonValue] | None = None,
    ) -> VerificationResult:
        record = self._agents.service.repository.get_agent_run(agent_run_id)
        context = _bound_review_context(record)
        verification_id = _required_context_string(context, "verification_id")
        request = self._verification.get_request(verification_id)
        read_only = _required_context_bool(context, "read_only")
        verifier = _verifier_from_record(record, read_only=read_only)
        _require_exact_review_binding(request, record, verifier, context)
        self._verification.validate_verifier(verification_id, verifier)

        if record.status is AgentRunStatus.RUNNING:
            record = self._agents.finish_agent_run(
                agent_run_id,
                status=AgentRunStatus.SUCCEEDED,
                artifact_ids=output_artifact_ids,
                result_ids=output_result_ids,
                model_call_refs=model_call_refs,
                tool_invocation_refs=tool_invocation_refs,
                telemetry=telemetry,
            )
        elif record.status is AgentRunStatus.SUCCEEDED:
            if (
                any(
                    (
                        output_artifact_ids,
                        output_result_ids,
                        model_call_refs,
                        tool_invocation_refs,
                    )
                )
                or telemetry is not None
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "completed reviewer Agent run cannot be rewritten while recording review",
                )
        else:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"reviewer Agent run did not complete successfully: {record.status.value}",
            )

        return self._verification.record_agent_review(
            verification_id,
            verifier=verifier,
            outcome=outcome,
            findings=findings,
            evidence_artifact_ids=evidence_artifact_ids,
            checks_executed=checks_executed,
        )

    def _spec_is_read_only(
        self,
        capability_ids: tuple[str, ...],
        capability_versions: Mapping[str, str],
    ) -> bool:
        if not capability_ids:
            return True
        registry = self._agents.capability_registry
        if registry is None:
            return False
        inventory = {
            (capability.capability_id, capability.version): capability
            for capability in registry.inventory_capabilities()
        }
        for capability_id in capability_ids:
            version = capability_versions.get(capability_id)
            if version is None:
                return False
            capability = inventory.get((capability_id, version))
            if capability is None or capability.side_effects is not SideEffectClassification.NONE:
                return False
        return True


def _agent_verifier_ref(agent_id: str, revision: int) -> str:
    return f"agent:{agent_id}@{revision}"


def _verifier_from_record(record: AgentRunRecord, *, read_only: bool) -> VerifierIdentity:
    return VerifierIdentity(
        verifier_ref=_agent_verifier_ref(record.agent.agent_id, record.agent.revision),
        kind=VerifierKind.AGENT,
        agent_id=record.agent.agent_id,
        agent_revision=record.agent.revision,
        model_config_id=record.selected_model_config_id,
        provider_id=record.selected_provider_id,
        read_only=read_only,
    )


def _review_context(
    request: VerificationRequest,
    verifier: VerifierIdentity,
    capability_ids: tuple[str, ...],
    capability_versions: Mapping[str, str],
) -> dict[str, JsonValue]:
    return {
        "schema": _REVIEW_CONTEXT_SCHEMA,
        "verification_id": request.verification_id,
        "task_id": request.task_id,
        "policy_id": request.policy_id,
        "policy_version": request.policy_version,
        "stage_id": request.stage_id,
        "subject": {
            "type": request.subject.subject_type,
            "id": request.subject.subject_id,
            "revision": request.subject.revision,
            "digest": request.subject.digest,
        },
        "repair_attempt": request.repair_attempt,
        "correlation_id": request.correlation_id,
        "agent_id": verifier.agent_id,
        "agent_revision": verifier.agent_revision,
        "model_config_id": verifier.model_config_id,
        "provider_id": verifier.provider_id,
        "read_only": verifier.read_only,
        "capability_ids": list(capability_ids),
        "capability_versions": dict(capability_versions),
    }


def _bound_review_context(record: AgentRunRecord) -> Mapping[str, JsonValue]:
    context = record.verification_context
    if context.get("schema") != _REVIEW_CONTEXT_SCHEMA:
        raise ContractError(
            ErrorCode.CONFLICT,
            "Agent run is not canonically bound to a reviewer Verification",
        )
    return context


def _require_exact_review_binding(
    request: VerificationRequest,
    record: AgentRunRecord,
    verifier: VerifierIdentity,
    context: Mapping[str, JsonValue],
) -> None:
    expected = _review_context(request, verifier, record.capability_ids, record.capability_versions)
    if dict(context) != expected:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "reviewer Agent run no longer matches its exact Verification binding",
        )
    if record.task_id != request.task_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "reviewer Agent run task differs from Verification request",
        )


def _required_context_string(context: Mapping[str, JsonValue], field: str) -> str:
    value = context.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"reviewer Agent verification context has invalid {field}",
        )
    return value


def _required_context_bool(context: Mapping[str, JsonValue], field: str) -> bool:
    value = context.get(field)
    if not isinstance(value, bool):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"reviewer Agent verification context has invalid {field}",
        )
    return value
