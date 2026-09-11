from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRunStatus,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import _run_startup_recovery
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
)
from ai_multi_agent_platform.testing import FakeModelProvider
from ai_multi_agent_platform.verification import (
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.verification.reviewer_agent import ReviewerAgentRuntime
from ai_multi_agent_platform.verification.reviewer_recovery import (
    ReviewerRecoveryDisposition,
)

_STAGED_DECISION_KEY = "automatic_reviewer_decision"


def _reviewer_profile() -> AgentProfile:
    return AgentProfile(
        name="Durable Restart Reviewer",
        role="reviewer",
        instructions=AgentInstructions(
            role=InstructionSource(content="Review the exact canonical output only."),
        ),
    )


def test_staged_reviewer_decision_survives_full_single_node_deployment_rebuild(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin("admin", "correct horse battery staple")

        provider = FakeModelProvider(
            response_text='{"outcome":"pass","findings":[]}',
            model_ref="issue-758-durable-local-reviewer",
        )
        first.models.register_provider(provider)
        first.models.register_model(
            ModelConfiguration(
                config_id="issue-758-durable-review-model",
                display_name="Issue 758 durable local review model",
                provider_id=provider.descriptor.provider_id,
                location=ModelLocation.LOCAL,
                health=HealthStatus.HEALTHY,
                capabilities=ModelCapabilities(
                    context_window=8_192,
                    structured_output=True,
                    modalities=("text",),
                ),
            )
        )
        reviewer = first.agents.create_agent(
            _reviewer_profile(),
            owner_ref=OwnerRef(type="user", id=admin.user_id),
        )

        task = await first.kernel.create_task(
            idempotency_key="issue-758-durable:create",
            title="Durable reviewer recovery",
            objective="Persist one reviewer decision and recover it after process rebuild.",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await first.kernel.ready_task(
            idempotency_key="issue-758-durable:ready",
            task_id=task.task_id,
        )
        run = await first.kernel.start_task(
            idempotency_key="issue-758-durable:start",
            task_id=task.task_id,
        )
        run = await first.kernel.refresh_run(
            idempotency_key="issue-758-durable:refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        assert run.status is RunStatus.SUCCEEDED

        result_id = new_id("result")
        await first.kernel.attach_result(
            idempotency_key="issue-758-durable:attach-result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
        policy = first.verification.register_policy(
            VerificationPolicy(
                name="durable reviewer process restart",
                stages=(VerificationStage("review", VerifierKind.AGENT),),
            )
        )
        request = await first.verification_runtime.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=result_id,
            correlation_id="issue-758-durable-review",
        )

        bridge = ReviewerAgentRuntime(
            first.verification,
            first.agent_runtime,
            evidence=first.verification_runtime.evidence,
            canonical_runtime=first.verification_runtime,
        )
        reviewer_run = await bridge.start_review(
            request.verification_id,
            run_id=run.run_id,
            agent_id=reviewer.agent_id,
            revision=reviewer.revision,
        )
        telemetry = dict(reviewer_run.telemetry)
        telemetry[_STAGED_DECISION_KEY] = {
            "schema": "automatic-reviewer-decision-v1",
            "outcome": VerificationOutcome.PASS.value,
            "findings": [],
            "evidence_artifact_ids": [],
            "checks_executed": ["agent_review"],
            "output_artifact_ids": [],
            "output_result_ids": [],
            "model_call_refs": [],
            "tool_invocation_refs": [],
        }
        first.agents.repository.update_agent_run(replace(reviewer_run, telemetry=telemetry))
        assert first.verification.result_for(request.verification_id) is None
        assert provider.calls == []

        restarted = build_single_node_deployment(config)
        restored_before = restarted.agents.repository.list_agent_runs()
        matching_before = [
            record
            for record in restored_before
            if record.verification_context.get("verification_id") == request.verification_id
        ]
        assert len(matching_before) == 1
        assert matching_before[0].status is AgentRunStatus.RUNNING
        assert _STAGED_DECISION_KEY in matching_before[0].telemetry
        assert restarted.verification.result_for(request.verification_id) is None

        startup = await _run_startup_recovery(restarted)

        recovered = next(
            record
            for record in startup.reviewer_recoveries
            if record.verification_id == request.verification_id
        )
        assert recovered.disposition is ReviewerRecoveryDisposition.STAGED_DECISION_REUSED
        assert startup.ready_for_service is True
        canonical = restarted.verification.result_for(request.verification_id)
        assert canonical is not None
        assert canonical.outcome is VerificationOutcome.PASS
        matching_after = [
            record
            for record in restarted.agents.repository.list_agent_runs()
            if record.verification_context.get("verification_id") == request.verification_id
        ]
        assert len(matching_after) == 1
        assert matching_after[0].agent_run_id == reviewer_run.agent_run_id
        assert matching_after[0].status is AgentRunStatus.SUCCEEDED
        assert provider.calls == []

        restarted_again = build_single_node_deployment(config)
        repeated = await _run_startup_recovery(restarted_again)
        repeated_record = next(
            record
            for record in repeated.reviewer_recoveries
            if record.verification_id == request.verification_id
        )
        assert repeated_record.disposition is ReviewerRecoveryDisposition.ALREADY_COMPLETED
        matching_repeated = [
            record
            for record in restarted_again.agents.repository.list_agent_runs()
            if record.verification_context.get("verification_id") == request.verification_id
        ]
        assert len(matching_repeated) == 1
        assert matching_repeated[0].agent_run_id == reviewer_run.agent_run_id
        assert matching_repeated[0].status is AgentRunStatus.SUCCEEDED
        repeated_result = restarted_again.verification.result_for(request.verification_id)
        assert repeated_result == canonical
        assert provider.calls == []

    asyncio.run(scenario())