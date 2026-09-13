from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus, ModelRequest, ModelResponse
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import (
    REFERENCE_MULTI_AGENT_CONSTRAINT,
)
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelLocation
from ai_multi_agent_platform.planning import ProposalStatus
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.verification.repair import VERIFICATION_REPAIR_SOURCE

_PASSWORD = "correct horse battery staple for issue 889"
_MODEL_ID = "model-issue-889-reference-verification"
_STAGE_ID = "reference-result-review"


class _ReviewAwareFakeModelProvider(FakeModelProvider):
    def __init__(self, review_outcomes: tuple[VerificationOutcome, ...]) -> None:
        super().__init__(response_text="canonical reference agent output")
        self._review_outcomes = list(review_outcomes)
        self.review_calls: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.request_id.endswith(":review-model"):
            self.calls.append(request)
            self.review_calls.append(request)
            if not self._review_outcomes:
                raise AssertionError("unexpected automatic reviewer model call")
            outcome = self._review_outcomes.pop(0)
            findings = []
            if outcome is not VerificationOutcome.PASS:
                findings = [
                    {
                        "code": f"reference_{outcome.value}",
                        "message": "Reference golden-path reviewer rejected the exact subject.",
                        "severity": "error",
                    }
                ]
            return ModelResponse(
                request_id=request.request_id,
                text=json.dumps({"outcome": outcome.value, "findings": findings}),
                model_ref=self.model_ref,
            )
        return await super().generate(request)


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the reference multi-agent task.",
                version="1",
            )
        ),
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_local_model(deployment: Any, provider: FakeModelProvider) -> None:
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id=_MODEL_ID,
            display_name="Issue 889 reference verification model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                structured_output=True,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )


def _create_agents(deployment: Any, owner: OwnerRef) -> dict[str, AgentRevisionRef]:
    refs: dict[str, AgentRevisionRef] = {}
    for role, name in (
        ("researcher", "Issue 889 Research Agent"),
        ("developer", "Issue 889 Execution Agent"),
        ("reviewer", "Issue 889 Review Agent"),
    ):
        revision = deployment.agents.create_agent(_profile(name, role), owner_ref=owner)
        ref = AgentRevisionRef(revision.agent_id, revision.revision)
        refs[role] = ref
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_principal(ref),
                actor_types=frozenset({ActorType.AGENT}),
                allowed_actions=frozenset(
                    {AuthorizationAction.READ, AuthorizationAction.RESULT_READ}
                ),
                resource_types=frozenset({ResourceType.ARTIFACT, ResourceType.GENERIC}),
            )
        )
    return refs


def _register_verification_policy(
    deployment: Any,
    reviewer: AgentRevisionRef,
    *,
    max_repair_attempts: int = 0,
):
    return deployment.verification.register_policy(
        VerificationPolicy(
            name="Issue 889 exact reference result review",
            stages=(VerificationStage(stage_id=_STAGE_ID, verifier_kind=VerifierKind.AGENT),),
            independence=ReviewerIndependence(producer_agent_must_differ=True),
            max_repair_attempts=max_repair_attempts,
            metadata={
                "automatic_reviewer": {
                    "enabled": True,
                    "subject_types": ["result"],
                    "stages": {
                        _STAGE_ID: {
                            "agent_id": reviewer.agent_id,
                            "agent_revision": reviewer.revision,
                        }
                    },
                }
            },
        )
    )


async def _run_reference_task(
    deployment: Any,
    *,
    owner_id: str,
    reviewer: AgentRevisionRef,
    suffix: str,
    max_repair_attempts: int = 0,
):
    task = await deployment.kernel.create_task(
        idempotency_key=f"issue-889:{suffix}:create",
        title=f"Issue 889 verification {suffix}",
        objective="Research, execute, review, and verify the exact reference result.",
        owner_type="user",
        owner_id=owner_id,
    )
    await deployment.kernel.ready_task(
        idempotency_key=f"issue-889:{suffix}:ready",
        task_id=task.task_id,
    )
    policy = _register_verification_policy(
        deployment,
        reviewer,
        max_repair_attempts=max_repair_attempts,
    )
    deployment.verification_runtime.require_task(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    proposal = await deployment.planning.propose(
        task_id=task.task_id,
        idempotency_key=f"issue-889:{suffix}:propose",
        task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
    )
    assert proposal.status is ProposalStatus.VALIDATED
    activated = await deployment.planning.activate(
        proposal.proposal.proposal_id,
        idempotency_key=f"issue-889:{suffix}:activate",
        actor=ActorIdentity(owner_id, ActorType.HUMAN),
    )
    assert activated.activation_plan_id is not None
    return task, policy, proposal, activated


def _execute_lineage(deployment: Any, plan_id: str):
    state = deployment.coordination_repository.get_plan(plan_id)
    execute_step = next(
        step for step in state.steps if step.title == "Produce the requested result"
    )
    record = deployment.coordination_repository.get_step_record(execute_step.id)
    assert record.latest_run_id is not None
    producer_runs = [
        item
        for item in deployment.agents.repository.list_agent_runs(record.latest_run_id)
        if "verification_id" not in item.verification_context
    ]
    assert len(producer_runs) == 1
    producer = producer_runs[0]
    assert len(producer.result_ids) == 1
    return execute_step, record.latest_run_id, producer, producer.result_ids[0]


def test_reference_golden_path_accepts_only_after_exact_result_passes_verification(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        provider = _ReviewAwareFakeModelProvider(
            (VerificationOutcome.PASS, VerificationOutcome.PASS, VerificationOutcome.PASS)
        )
        _install_local_model(deployment, provider)
        admin = deployment.bootstrap_admin("issue-889-verification-pass", _PASSWORD)
        refs = _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))

        task, policy, _proposal, activated = await _run_reference_task(
            deployment,
            owner_id=admin.user_id,
            reviewer=refs["reviewer"],
            suffix="verification-pass",
        )
        completed = await deployment.kernel.get_task(task.task_id)
        assert completed.status is TaskStatus.SUCCEEDED

        _execute_step, execute_run_id, producer, result_id = _execute_lineage(
            deployment,
            activated.activation_plan_id,
        )
        assert producer.agent == refs["developer"]
        run = await deployment.kernel.get_run(task.task_id, execute_run_id)
        assert result_id in run.result_ids
        exact_subject = await deployment.verification_runtime.evidence.resolve_subject(
            task_id=task.task_id,
            subject_type="result",
            subject_id=result_id,
        )

        matches = [
            (request, result)
            for request, result in deployment.verification.history(task_id=task.task_id)
            if request.policy_id == policy.policy_id
            and request.policy_version == policy.version
            and request.stage_id == _STAGE_ID
            and request.subject.subject_id == result_id
        ]
        assert len(matches) == 1
        request, result = matches[0]
        assert request.subject == exact_subject
        assert request.run_id == execute_run_id
        assert request.producer is not None
        assert request.producer.agent_id == refs["developer"].agent_id
        assert request.producer.agent_revision == refs["developer"].revision
        assert result is not None
        assert result.subject == exact_subject
        assert result.outcome is VerificationOutcome.PASS

        reviewer_runs = [
            item
            for item in deployment.agents.repository.list_agent_runs(execute_run_id)
            if item.verification_context.get("verification_id") == request.verification_id
        ]
        assert len(reviewer_runs) == 1
        reviewer_run = reviewer_runs[0]
        assert reviewer_run.status is AgentRunStatus.SUCCEEDED
        assert reviewer_run.agent == refs["reviewer"]
        assert reviewer_run.model_call_refs == (f"{reviewer_run.agent_run_id}:review-model",)

        history = await deployment.kernel.history(task.task_id)
        succeeded = [event for event in history if event.event_type == "task.succeeded"]
        assert len(succeeded) == 1
        assert len(provider.review_calls) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome",
    [VerificationOutcome.FAIL, VerificationOutcome.INCONCLUSIVE],
)
def test_reference_golden_path_non_pass_verification_cannot_complete_task(
    tmp_path: Path,
    outcome: VerificationOutcome,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / outcome.value, secure_cookie=False)
        )
        provider = _ReviewAwareFakeModelProvider(
            (VerificationOutcome.PASS, VerificationOutcome.PASS, outcome)
        )
        _install_local_model(deployment, provider)
        admin = deployment.bootstrap_admin(f"issue-889-verification-{outcome.value}", _PASSWORD)
        refs = _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))

        task, policy, _proposal, activated = await _run_reference_task(
            deployment,
            owner_id=admin.user_id,
            reviewer=refs["reviewer"],
            suffix=f"verification-{outcome.value}",
        )
        blocked = await deployment.kernel.get_task(task.task_id)
        assert blocked.status is TaskStatus.WAITING
        assert blocked.blocked is True
        expected_wait_reason = (
            "verification:rejected"
            if outcome is VerificationOutcome.FAIL
            else "verification:waiting"
        )
        assert blocked.wait_reason == expected_wait_reason

        _execute_step, execute_run_id, _producer, result_id = _execute_lineage(
            deployment,
            activated.activation_plan_id,
        )
        exact_subject = await deployment.verification_runtime.evidence.resolve_subject(
            task_id=task.task_id,
            subject_type="result",
            subject_id=result_id,
        )
        matches = [
            (request, result)
            for request, result in deployment.verification.history(task_id=task.task_id)
            if request.policy_id == policy.policy_id
            and request.stage_id == _STAGE_ID
            and request.subject == exact_subject
        ]
        assert len(matches) == 1
        request, result = matches[0]
        assert request.run_id == execute_run_id
        assert result is not None
        assert result.subject == exact_subject
        assert result.outcome is outcome

        history = await deployment.kernel.history(task.task_id)
        assert "task.succeeded" not in [event.event_type for event in history]
        assert len(provider.review_calls) == 3

    asyncio.run(scenario())


def test_reference_golden_path_repairs_needs_changes_and_reverifies_exact_new_revision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "repair", secure_cookie=False)
        )
        provider = _ReviewAwareFakeModelProvider(
            (
                VerificationOutcome.PASS,
                VerificationOutcome.PASS,
                VerificationOutcome.NEEDS_CHANGES,
                VerificationOutcome.PASS,
            )
        )
        _install_local_model(deployment, provider)
        admin = deployment.bootstrap_admin("issue-889-verification-repair", _PASSWORD)
        refs = _create_agents(deployment, OwnerRef(type="user", id=admin.user_id))

        task, policy, _proposal, activated = await _run_reference_task(
            deployment,
            owner_id=admin.user_id,
            reviewer=refs["reviewer"],
            suffix="verification-repair",
            max_repair_attempts=1,
        )
        completed = await deployment.kernel.get_task(task.task_id)
        assert completed.status is TaskStatus.SUCCEEDED

        _execute_step, execute_run_id, producer, result_a_id = _execute_lineage(
            deployment,
            activated.activation_plan_id,
        )
        assert producer.agent == refs["developer"]
        history = [
            (request, result)
            for request, result in deployment.verification.history(task_id=task.task_id)
            if request.policy_id == policy.policy_id
            and request.policy_version == policy.version
            and request.stage_id == _STAGE_ID
        ]
        repair_lineage = [
            pair
            for pair in history
            if pair[0].subject.subject_id == result_a_id or pair[0].repair_attempt > 0
        ]
        assert len(repair_lineage) == 2
        first_request, first_result = repair_lineage[0]
        second_request, second_result = repair_lineage[1]
        assert first_result is not None
        assert second_result is not None
        assert first_request.run_id == execute_run_id
        assert first_request.repair_attempt == 0
        assert first_result.outcome is VerificationOutcome.NEEDS_CHANGES
        assert second_request.repair_attempt == 1
        assert second_result.outcome is VerificationOutcome.PASS

        subject_a = first_request.subject
        subject_b = second_request.subject
        assert subject_a.subject_id == result_a_id
        assert subject_b.subject_id != subject_a.subject_id
        assert subject_b.revision != subject_a.revision
        assert subject_b.digest != subject_a.digest
        assert second_result.subject == subject_b
        assert second_request.run_id is not None
        assert second_request.run_id != execute_run_id

        repair_run = await deployment.kernel.get_run(task.task_id, second_request.run_id)
        assert repair_run.status is RunStatus.SUCCEEDED
        assert repair_run.run.subject_type == "step"
        assert subject_b.subject_id in repair_run.result_ids

        repair_created = [
            event
            for event in await deployment.kernel.history(task.task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == VERIFICATION_REPAIR_SOURCE
        ]
        assert len(repair_created) == 1
        assert repair_created[0].subject_id == second_request.run_id
        assert len(provider.review_calls) == 4

    asyncio.run(scenario())
