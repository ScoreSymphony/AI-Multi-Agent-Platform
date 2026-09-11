from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    STANDARD_TEAM_IDS,
    AgentRunStatus,
    bootstrap_standard_agents,
    clone_standard_team,
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

_PASSWORD = "correct horse battery staple"
_MODEL_ID = "model-local-team-review"
_PROVIDER_ID = "local-team-review-provider"
_PROVIDER_MODEL = "qwen-team-review"


class _PassTransport:
    """Drive one local Planner output and one canonical reviewer PASS."""

    def __init__(self) -> None:
        self.chat_calls = 0

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del method, headers, payload, timeout_seconds
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": _PROVIDER_MODEL}]})
        if not url.endswith("/chat/completions"):
            raise AssertionError(f"unexpected model endpoint: {url}")

        self.chat_calls += 1
        if self.chat_calls == 1:
            content = "canonical planner result for automatic team review"
        elif self.chat_calls == 2:
            content = json.dumps(
                {
                    "outcome": "pass",
                    "findings": [
                        {
                            "code": "team_review_passed",
                            "message": "The exact result satisfies the configured review stage.",
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
        "display_name": "Local Team Review Model",
        "base_url": "http://127.0.0.1:8004/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


async def _exercise_team_review(
    tmp_path: Path,
    *,
    use_cloned_team: bool,
) -> None:
    transport = _PassTransport()
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )
    admin = deployment.bootstrap_admin("admin", _PASSWORD)
    token = deployment.authentication.create_personal_access_token(
        admin.user_id,
        purpose="automatic-team-review-integration",
    ).secret

    configured = await deployment.http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/commands/onboarding.configure-model",
            headers=_headers(token, key="automatic-team-review:model"),
            body=_model_payload(),
        )
    )
    assert configured.status == 200, configured.body

    project_response = await deployment.http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/projects",
            headers=_headers(token, key="automatic-team-review:project"),
            body={"name": "Automatic reviewer Team project"},
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
            headers=_headers(token, key="automatic-team-review:workspace"),
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
    if use_cloned_team:
        team = clone_standard_team(
            deployment.agents,
            "software_development",
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            name="Custom Software Review Team",
        )
        team = deployment.agents.update_team(
            team.team_id,
            replace(team.profile, description="Current custom reviewer Team revision"),
        )
        team_id = team.team_id
        team_revision = team.revision
        reviewer_route: dict[str, JsonValue] = {
            "candidate_team_ids": [team_id],
            "reviewer_role": "reviewer_tester",
        }
    else:
        team_id = STANDARD_TEAM_IDS["software_development"]
        team_revision = 1
        reviewer_route = {
            "team_id": team_id,
            "team_revision": team_revision,
            "team_role": "reviewer_tester",
        }

    created = await deployment.http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=_headers(token, key="automatic-team-review:task"),
            body={
                "title": "Automatic AgentTeam reviewer Task",
                "objective": "Produce a planner result and independently verify it.",
                "project_id": project_id,
            },
        )
    )
    assert created.status == 201, created.body
    assert isinstance(created.body, dict)
    task_id = created.body["id"]
    assert isinstance(task_id, str)

    await deployment.kernel.update_task(
        idempotency_key="automatic-team-review:agent-binding",
        task_id=task_id,
        metadata=encode_agent_execution_binding(
            AgentExecutionBinding(
                agent_id=STANDARD_AGENT_IDS["planner"],
                agent_revision=1,
                model_config_id=_MODEL_ID,
                workspace_id=workspace_id,
            )
        ),
        actor_ref=admin.user_id,
        source="conformance",
    )

    policy = deployment.verification.register_policy(
        VerificationPolicy(
            name="Automatic AgentTeam verification",
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
            metadata={
                "automatic_reviewer": {
                    "enabled": True,
                    "subject_types": ["result"],
                    "stages": {"agent-review": reviewer_route},
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
            headers=_headers(token, key="automatic-team-review:queue"),
        )
    )
    assert queued.status == 200, queued.body
    started = await deployment.http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/tasks/{task_id}:start",
            headers=_headers(token, key="automatic-team-review:start"),
            body={
                "workspace_id": workspace_id,
                "workspace_snapshot_id": workspace_snapshot_id,
            },
        )
    )
    assert started.status == 200, started.body
    assert isinstance(started.body, dict)
    run_id = started.body["id"]
    assert isinstance(run_id, str)

    producer_run = await deployment.kernel.refresh_run(
        idempotency_key="automatic-team-review:refresh",
        task_id=task_id,
        run_id=run_id,
        actor_ref=admin.user_id,
        source="conformance",
    )
    assert producer_run.status is RunStatus.SUCCEEDED
    result_id = producer_run.output.get("result_id")
    assert isinstance(result_id, str)

    waiting = await deployment.kernel.get_task(task_id)
    assert waiting.status is TaskStatus.WAITING
    assert waiting.wait_reason == "verification:waiting"

    completed = await deployment.kernel.attach_result(
        idempotency_key="automatic-team-review:result",
        task_id=task_id,
        run_id=run_id,
        result_id=result_id,
        actor_ref=admin.user_id,
        source="conformance",
    )
    assert completed.status is TaskStatus.SUCCEEDED
    assert transport.chat_calls == 2

    history = [
        pair
        for pair in deployment.verification.history(task_id=task_id)
        if pair[0].policy_id == policy.policy_id
        and pair[0].policy_version == policy.version
        and pair[0].stage_id == "agent-review"
    ]
    assert len(history) == 1
    request, result = history[0]
    assert result is not None
    assert result.outcome is VerificationOutcome.PASS
    assert request.subject.subject_id == result_id
    assert result.subject == request.subject
    assert result.verifier.agent_id == STANDARD_AGENT_IDS["reviewer"]
    assert result.verifier.agent_revision == 1
    assert result.verifier.model_config_id == _MODEL_ID
    assert result.verifier.provider_id == _PROVIDER_ID

    reviewer_runs = [
        record
        for record in deployment.agents.repository.list_agent_runs()
        if record.agent.agent_id == STANDARD_AGENT_IDS["reviewer"]
        and record.verification_context.get("verification_id") == request.verification_id
    ]
    assert len(reviewer_runs) == 1
    reviewer_run = reviewer_runs[0]
    assert reviewer_run.status is AgentRunStatus.SUCCEEDED
    assert reviewer_run.team is not None
    assert reviewer_run.team.team_id == team_id
    assert reviewer_run.team.revision == team_revision
    assert reviewer_run.verification_context.get("subject") == {
        "type": "result",
        "id": result_id,
        "revision": request.subject.revision,
        "digest": request.subject.digest,
    }

    if use_cloned_team:
        automatic = policy.metadata["automatic_reviewer"]
        assert isinstance(automatic, Mapping)
        stages = automatic["stages"]
        assert isinstance(stages, Mapping)
        route = stages["agent-review"]
        assert isinstance(route, Mapping)
        assert route["candidate_team_ids"] == [team_id]
        assert route["reviewer_role"] == "reviewer_tester"
        # These persisted policy facts plus the exact Verification stage and AgentRun Team/revision
        # are sufficient to explain the deterministic scoped discovery decision after the fact.
        assert request.stage_id == "agent-review"
        assert reviewer_run.team.team_id in route["candidate_team_ids"]
        assert reviewer_run.team.revision == team_revision == 2


def test_standard_software_development_team_runs_productive_reviewer_tester_flow(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise_team_review(tmp_path, use_cloned_team=False))


def test_cloned_software_development_team_uses_scoped_role_discovery(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise_team_review(tmp_path, use_cloned_team=True))
