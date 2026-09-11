from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentRunStatus,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.verification.repair import VERIFICATION_REPAIR_SOURCE

_PASSWORD = "correct horse battery staple"
_MODEL_ID = "model-local-auto-repair"
_PROVIDER_ID = "local-auto-repair-provider"
_PROVIDER_MODEL = "qwen-auto-repair"


class _RepairTransport:
    """Drive Producer -> NEEDS_CHANGES -> repair Producer -> PASS deterministically."""

    def __init__(self) -> None:
        self.chat_calls = 0
        self.repair_received_findings = False

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del method, headers, timeout_seconds
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": _PROVIDER_MODEL}]})
        if not url.endswith("/chat/completions"):
            raise AssertionError(f"unexpected model endpoint: {url}")

        self.chat_calls += 1
        if self.chat_calls == 1:
            content = "candidate output without the required conclusion"
        elif self.chat_calls == 2:
            content = json.dumps(
                {
                    "outcome": "needs_changes",
                    "findings": [
                        {
                            "code": "missing_requirement",
                            "message": "Add the required conclusion.",
                            "severity": "error",
                        }
                    ],
                }
            )
        elif self.chat_calls == 3:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            assert "missing_requirement" in encoded
            assert "Add the required conclusion." in encoded
            assert "untrusted diagnostic evidence" in encoded
            assert "Produce a local candidate and require independent repair review." in encoded
            self.repair_received_findings = True
            content = "repaired candidate with the required conclusion"
        elif self.chat_calls == 4:
            content = json.dumps(
                {
                    "outcome": "pass",
                    "findings": [
                        {
                            "code": "repair_verified",
                            "message": "The repaired candidate satisfies the requirement.",
                            "severity": "info",
                        }
                    ],
                }
            )
        else:
            raise AssertionError(f"unexpected extra model invocation: {self.chat_calls}")

        return HttpJsonResponse(
            200,
            {
                "model": _PROVIDER_MODEL,
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 5,
                    "total_tokens": 13,
                },
            },
        )


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def _model_payload() -> dict[str, JsonValue]:
    return {
        "resource_ref": FIRST_RUN_RESOURCE_ID,
        "adapter_id": "openai-compatible",
        "provider_id": _PROVIDER_ID,
        "model_config_id": _MODEL_ID,
        "provider_model": _PROVIDER_MODEL,
        "display_name": "Local Automatic Repair Model",
        "base_url": "http://127.0.0.1:8003/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def test_public_single_node_runs_bounded_agent_repair_and_fresh_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _RepairTransport()
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="automatic-review-repair-integration",
        ).secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="automatic-review-repair:model"),
                body=_model_payload(),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="automatic-review-repair:project"),
                body={"name": "Automatic reviewer repair project"},
            )
        )
        assert project_response.status == 201, project_response.body
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str)

        workspace_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=_headers(token, key="automatic-review-repair:workspace"),
                body={"project_id": project_id, "workspace_type": "isolated_run"},
            )
        )
        assert workspace_response.status == 201, workspace_response.body
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        workspace_snapshot_id = workspace_response.body["base_snapshot_id"]
        assert isinstance(workspace_id, str)
        assert isinstance(workspace_snapshot_id, str)

        bootstrap_standard_agents(deployment.agents)
        producer = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
            name="Automatic Repair Producer",
        )
        reviewer = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["reviewer"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
            name="Automatic Repair Reviewer",
        )

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers(token, key="automatic-review-repair:task"),
                body={
                    "title": "Automatic reviewer repair Task",
                    "objective": (
                        "Produce a local candidate and require independent repair review."
                    ),
                    "project_id": project_id,
                },
            )
        )
        assert created.status == 201, created.body
        assert isinstance(created.body, dict)
        task_id = created.body["id"]
        assert isinstance(task_id, str)

        await deployment.kernel.update_task(
            idempotency_key="automatic-review-repair:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=producer.agent_id,
                    agent_revision=producer.revision,
                    model_config_id=_MODEL_ID,
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="Automatic Agent repair verification",
                stages=(
                    VerificationStage(
                        stage_id="agent-review",
                        verifier_kind=VerifierKind.AGENT,
                    ),
                ),
                independence=ReviewerIndependence(
                    producer_agent_must_differ=True,
                    agent_reviewer_must_be_read_only=True,
                ),
                max_repair_attempts=1,
                metadata={
                    "automatic_reviewer": {
                        "enabled": True,
                        "subject_types": ["result"],
                        "stages": {
                            "agent-review": {
                                "agent_id": reviewer.agent_id,
                                "agent_revision": reviewer.revision,
                            }
                        },
                    }
                },
            )
        )
        deployment.verification_runtime.require_task(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )

        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=_headers(token, key="automatic-review-repair:queue"),
            )
        )
        assert queued.status == 200, queued.body
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="automatic-review-repair:start"),
                body={
                    "workspace_id": workspace_id,
                    "workspace_snapshot_id": workspace_snapshot_id,
                },
            )
        )
        assert started.status == 200, started.body
        assert isinstance(started.body, dict)
        original_run_id = started.body["id"]
        assert isinstance(original_run_id, str)

        original_run = await deployment.kernel.refresh_run(
            idempotency_key="automatic-review-repair:refresh",
            task_id=task_id,
            run_id=original_run_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert original_run.status is RunStatus.SUCCEEDED
        original_result_id = original_run.output.get("result_id")
        assert isinstance(original_result_id, str)

        waiting = await deployment.kernel.get_task(task_id)
        assert waiting.status is TaskStatus.WAITING
        assert waiting.wait_reason == "verification:waiting"

        completed = await deployment.kernel.attach_result(
            idempotency_key="automatic-review-repair:result",
            task_id=task_id,
            run_id=original_run_id,
            result_id=original_result_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert completed.status is TaskStatus.SUCCEEDED
        assert transport.chat_calls == 4
        assert transport.repair_received_findings is True

        history = [
            pair
            for pair in deployment.verification.history(task_id=task_id)
            if pair[0].policy_id == policy.policy_id
            and pair[0].policy_version == policy.version
            and pair[0].stage_id == "agent-review"
        ]
        assert len(history) == 2
        first_request, first_result = history[0]
        second_request, second_result = history[1]
        assert first_result is not None
        assert second_result is not None
        assert first_result.outcome is VerificationOutcome.NEEDS_CHANGES
        assert second_result.outcome is VerificationOutcome.PASS
        assert first_request.repair_attempt == 0
        assert second_request.repair_attempt == 1
        assert first_request.subject.subject_id == original_result_id
        assert second_request.subject.subject_id != original_result_id
        assert first_request.subject != second_request.subject
        assert second_request.run_id is not None
        assert second_request.run_id != original_run_id

        repair_run = await deployment.kernel.get_run(task_id, second_request.run_id)
        assert repair_run.run.subject_type == "step"
        assert repair_run.status is RunStatus.SUCCEEDED
        assert second_request.subject.subject_id in repair_run.result_ids

        repair_created = [
            event
            for event in await deployment.kernel.history(task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == VERIFICATION_REPAIR_SOURCE
        ]
        assert len(repair_created) == 1
        repair_waits = [
            event
            for event in await deployment.kernel.history(task_id)
            if event.event_type == "task.waiting"
            and event.payload.get("reason") == "verification:repair_required"
        ]
        assert len(repair_waits) >= 1

        producer_runs = [
            record
            for record in deployment.agents.repository.list_agent_runs()
            if record.agent.agent_id == producer.agent_id
        ]
        reviewer_runs = [
            record
            for record in deployment.agents.repository.list_agent_runs()
            if record.agent.agent_id == reviewer.agent_id
        ]
        assert len(producer_runs) == 2
        assert len(reviewer_runs) == 2
        assert all(record.status is AgentRunStatus.SUCCEEDED for record in producer_runs)
        assert all(record.status is AgentRunStatus.SUCCEEDED for record in reviewer_runs)
        assert {record.run_id for record in producer_runs} == {
            original_run_id,
            second_request.run_id,
        }

        repeated = await deployment.kernel.attach_result(
            idempotency_key="automatic-review-repair:result",
            task_id=task_id,
            run_id=original_run_id,
            result_id=original_result_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert repeated.status is TaskStatus.SUCCEEDED
        assert transport.chat_calls == 4
        assert (
            len(
                [
                    pair
                    for pair in deployment.verification.history(task_id=task_id)
                    if pair[0].policy_id == policy.policy_id
                    and pair[0].policy_version == policy.version
                    and pair[0].stage_id == "agent-review"
                ]
            )
            == 2
        )

    asyncio.run(scenario())
