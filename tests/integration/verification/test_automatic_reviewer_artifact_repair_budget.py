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
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    CapabilityConstraint,
    InstructionSource,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.capabilities import (
    ISOLATED_WORKSPACE_WRITE_FEATURE,
    CapabilityRegistration,
    CapabilitySpec,
    CapabilityToolProvider,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.verification import (
    CompletionState,
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.verification.repair import VERIFICATION_REPAIR_SOURCE

_PASSWORD = "issue-759-budget-password"
_MODEL_ID = "model-local-artifact-budget"
_PROVIDER_ID = "local-artifact-budget-provider"
_PROVIDER_MODEL = "qwen-artifact-budget"
_ARTIFACT_CAPABILITY_ID = "tool.test.write_budget_artifact"
_ARTIFACT_TOOL_REF = "test.write_budget_artifact"


class _BudgetExhaustionTransport:
    """Drive Artifact v1 -> repair v2 -> NEEDS_CHANGES with no second repair."""

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
        assert payload is not None

        self.chat_calls += 1
        if self.chat_calls in {1, 3}:
            tools = payload.get("tools")
            assert isinstance(tools, list) and len(tools) == 1
            tool = tools[0]
            assert isinstance(tool, Mapping)
            function = tool.get("function")
            assert isinstance(function, Mapping)
            tool_name = function.get("name")
            assert isinstance(tool_name, str)

            if self.chat_calls == 1:
                content = "artifact-v1: incomplete conclusion\n"
                call_id = "call-budget-artifact-v1"
            else:
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                assert "artifact_missing_conclusion" in encoded
                assert "Add the required conclusion to the Artifact." in encoded
                self.repair_received_findings = True
                content = "artifact-v2: repaired once but still incomplete\n"
                call_id = "call-budget-artifact-v2"

            return HttpJsonResponse(
                200,
                {
                    "model": _PROVIDER_MODEL,
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": call_id,
                                        "type": "function",
                                        "function": {
                                            "name": tool_name,
                                            "arguments": json.dumps({"content": content}),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 6,
                        "total_tokens": 16,
                    },
                },
            )

        if self.chat_calls == 2:
            review = {
                "outcome": "needs_changes",
                "findings": [
                    {
                        "code": "artifact_missing_conclusion",
                        "message": "Add the required conclusion to the Artifact.",
                        "severity": "error",
                    }
                ],
            }
        elif self.chat_calls == 4:
            review = {
                "outcome": "needs_changes",
                "findings": [
                    {
                        "code": "artifact_still_incomplete",
                        "message": "The repaired Artifact still does not satisfy the requirement.",
                        "severity": "error",
                    }
                ],
            }
        else:
            raise AssertionError(f"unexpected extra model invocation: {self.chat_calls}")

        return HttpJsonResponse(
            200,
            {
                "model": _PROVIDER_MODEL,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(review),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 7,
                    "total_tokens": 16,
                },
            },
        )


class _CanonicalArtifactProvider(CapabilityToolProvider):
    def __init__(self, files) -> None:
        self._files = files
        self.outputs: list[tuple[str, str, bytes]] = []

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="test.canonical-budget-artifact",
            provider_type="native",
            supported_operations=("invoke", "discover"),
            capabilities=(
                Capability(
                    name=_ARTIFACT_CAPABILITY_ID,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id=_ARTIFACT_CAPABILITY_ID,
                    name="Write canonical budget Artifact",
                    description="Persist one isolated canonical File-backed Artifact.",
                    version="1.0",
                    input_schema={
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                    side_effects=SideEffectClassification.LOCAL_WRITE,
                    features=(ISOLATED_WORKSPACE_WRITE_FEATURE,),
                    health=HealthStatus.HEALTHY,
                    available=True,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref=_ARTIFACT_TOOL_REF,
                priority=100,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        assert invocation.tool_ref == _ARTIFACT_TOOL_REF
        assert invocation.task_id is not None
        assert invocation.run_id is not None
        arguments = invocation.arguments_json()
        content = arguments.get("content")
        assert isinstance(content, str)
        payload = content.encode("utf-8")
        owner_type = invocation.context.owner_type or "service"
        owner_id = invocation.context.owner_id or "automatic-reviewer-artifact-budget"
        context = DataAccessContext(
            operation=invocation.context,
            actor_ref=f"{owner_type}:{owner_id}",
            task_id=invocation.task_id,
            run_id=invocation.run_id,
        )
        record = await self._files.create_file(
            payload,
            context,
            content_type="text/plain",
            metadata={
                "source": "automatic-reviewer-artifact-budget-conformance",
                "data_classification": "internal",
            },
        )
        artifact_id = new_id("artifact")
        await self._files.link_artifact(record.file_id, artifact_id, context)
        self.outputs.append((record.file_id, artifact_id, payload))
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"file_id": record.file_id, "artifact_id": artifact_id},
            artifact_refs=(artifact_id,),
            evidence_refs=(record.file_id, artifact_id),
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
        "display_name": "Local Artifact Budget Model",
        "base_url": "http://127.0.0.1:8006/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": True,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def _producer_profile() -> AgentProfile:
    return AgentProfile(
        name="Canonical Artifact Budget Producer",
        role="artifact-producer",
        instructions=AgentInstructions(
            role=InstructionSource(
                content="Use the supplied capability to persist the requested Artifact."
            )
        ),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(modalities=("text",), tool_calling=True),
            allow_task_override=True,
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(_ARTIFACT_CAPABILITY_ID,),
            constraints=(
                CapabilityConstraint(
                    capability_id=_ARTIFACT_CAPABILITY_ID,
                    required=True,
                    exact_version="1.0",
                ),
            ),
        ),
    )


def test_productive_artifact_repair_budget_stops_after_one_iteration(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _BudgetExhaustionTransport()
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="automatic-artifact-budget-integration",
        ).secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="automatic-artifact-budget:model"),
                body=_model_payload(),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="automatic-artifact-budget:project"),
                body={"name": "Automatic Artifact budget project"},
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
                headers=_headers(token, key="automatic-artifact-budget:workspace"),
                body={"project_id": project_id, "workspace_type": "isolated_run"},
            )
        )
        assert workspace_response.status == 201, workspace_response.body
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        workspace_snapshot_id = workspace_response.body["base_snapshot_id"]
        assert isinstance(workspace_id, str)
        assert isinstance(workspace_snapshot_id, str)

        artifact_provider = _CanonicalArtifactProvider(deployment.files)
        await deployment.capabilities.register_provider(artifact_provider)
        bootstrap_standard_agents(deployment.agents)
        producer = deployment.agents.create_agent(
            _producer_profile(),
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
        )
        reviewer = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["reviewer"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
            name="Artifact Budget Reviewer",
        )

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers(token, key="automatic-artifact-budget:task"),
                body={
                    "title": "Automatic Artifact budget Task",
                    "objective": "Produce an Artifact and stop after the bounded repair budget.",
                    "project_id": project_id,
                },
            )
        )
        assert created.status == 201, created.body
        assert isinstance(created.body, dict)
        task_id = created.body["id"]
        assert isinstance(task_id, str)

        await deployment.kernel.update_task(
            idempotency_key="automatic-artifact-budget:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=producer.agent_id,
                    agent_revision=producer.revision,
                    model_config_id=_MODEL_ID,
                    capability_ids=(_ARTIFACT_CAPABILITY_ID,),
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="Automatic Artifact budget verification",
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
                        "subject_types": ["artifact"],
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
                headers=_headers(token, key="automatic-artifact-budget:queue"),
            )
        )
        assert queued.status == 200, queued.body
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="automatic-artifact-budget:start"),
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
            idempotency_key="automatic-artifact-budget:refresh",
            task_id=task_id,
            run_id=original_run_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert original_run.status is RunStatus.SUCCEEDED
        original_refs = original_run.output.get("artifact_refs")
        assert isinstance(original_refs, tuple | list)
        assert len(original_refs) == 1
        original_artifact_id = original_refs[0]
        assert isinstance(original_artifact_id, str)

        await deployment.kernel.attach_artifact(
            idempotency_key="automatic-artifact-budget:artifact-v1",
            task_id=task_id,
            run_id=original_run_id,
            artifact_id=original_artifact_id,
            actor_ref=admin.user_id,
            source="conformance",
        )

        assert transport.chat_calls == 4
        assert transport.repair_received_findings is True
        assert len(artifact_provider.outputs) == 2

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
        assert second_result.outcome is VerificationOutcome.NEEDS_CHANGES
        assert first_request.repair_attempt == 0
        assert second_request.repair_attempt == 1
        assert first_request.subject.subject_id != second_request.subject.subject_id
        assert first_request.subject.revision != second_request.subject.revision
        assert first_request.subject.digest != second_request.subject.digest

        completion = deployment.verification_runtime._completion.assess_task_completion(task_id)
        assert completion.state is CompletionState.REJECTED
        assert completion.reason == "verification repair limit exhausted"
        assert completion.repair_attempts_remaining == 0
        assert completion.blocking_verification_ids == (second_request.verification_id,)

        repair_created = [
            event
            for event in await deployment.kernel.history(task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == VERIFICATION_REPAIR_SOURCE
        ]
        assert len(repair_created) == 1

        repeated = await deployment.kernel.attach_artifact(
            idempotency_key="automatic-artifact-budget:artifact-v1",
            task_id=task_id,
            run_id=original_run_id,
            artifact_id=original_artifact_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        del repeated
        assert transport.chat_calls == 4
        assert len(artifact_provider.outputs) == 2
        repair_created_after_retry = [
            event
            for event in await deployment.kernel.history(task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == VERIFICATION_REPAIR_SOURCE
        ]
        assert len(repair_created_after_retry) == 1

    asyncio.run(scenario())
