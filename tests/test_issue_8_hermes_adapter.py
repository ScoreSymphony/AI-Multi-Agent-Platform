from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_ADAPTER_ID,
    HERMES_PINNED_REVISION,
    HermesAdapterConfig,
    HermesAgentMapper,
    HermesHttpResponse,
    HermesOrchestrator,
)
from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentExecutionSpec,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
    ReferenceOrchestratorMapper,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    OperationContext,
    OperationControl,
    PlanRequest,
    PlanResponse,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.testing import FakeOrchestrator

OWNER = OwnerRef(type="user", id="issue-8-test")


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    url: str
    payload: Mapping[str, JsonValue] | None
    headers: Mapping[str, str]
    timeout_seconds: float


class FakeHermesTransport:
    def __init__(self, responses: list[HermesHttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[RecordedRequest] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        self.calls.append(
            RecordedRequest(method, url, payload, dict(headers), timeout_seconds)
        )
        if not self.responses:
            raise AssertionError("unexpected Hermes HTTP request")
        return self.responses.pop(0)


def _plan_request() -> PlanRequest:
    return PlanRequest(
        task_id=new_id("task"),
        objective="Prepare a deterministic release plan",
        context=OperationContext(
            correlation_id="correlation-hermes-8",
            control=OperationControl(idempotency_key="platform-plan-1"),
        ),
    )


def _completed_plan_output() -> str:
    return json.dumps(
        {
            "summary": "Release safely",
            "steps": [
                {
                    "key": "inspect",
                    "title": "Inspect",
                    "objective": "Inspect current state",
                    "depends_on": [],
                },
                {
                    "key": "release",
                    "title": "Release",
                    "objective": "Publish the validated result",
                    "depends_on": ["inspect"],
                },
            ],
        }
    )


def test_hermes_orchestrator_maps_canonical_task_to_external_run_and_back() -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_hermes_123", "status": "started"}),
                HermesHttpResponse(
                    200,
                    {
                        "object": "hermes.run",
                        "run_id": "run_hermes_123",
                        "status": "running",
                    },
                ),
                HermesHttpResponse(
                    200,
                    {
                        "object": "hermes.run",
                        "run_id": "run_hermes_123",
                        "status": "completed",
                        "output": _completed_plan_output(),
                        "session_id": "session_hermes_456",
                    },
                ),
            ]
        )
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(
                enabled=True,
                base_url="http://hermes.internal:8642",
                poll_interval_seconds=0.001,
            ),
            transport=transport,
            secret_resolver=lambda name: "secret-key" if name == "API_SERVER_KEY" else None,
        )
        request = _plan_request()

        response = await orchestrator.plan(request)

        assert isinstance(response, PlanResponse)
        assert response.summary == "Release safely"
        assert [step.key for step in response.steps] == ["inspect", "release"]
        assert response.steps[1].depends_on == ("inspect",)
        assert request.task_id.startswith("task_")
        assert transport.calls[0].method == "POST"
        assert transport.calls[0].url == "http://hermes.internal:8642/v1/runs"
        assert transport.calls[0].headers["Idempotency-Key"] == "platform-plan-1"
        assert transport.calls[0].headers["authorization"] == "Bearer secret-key"
        assert transport.calls[0].headers["X-Correlation-Id"] == "correlation-hermes-8"
        assert transport.calls[0].payload is not None
        assert request.task_id in str(transport.calls[0].payload["input"])
        assert "Plan or Step IDs" in str(transport.calls[0].payload["instructions"])
        metadata = response.adapter_metadata[0]
        assert metadata.namespace == "hermes"
        assert metadata.values["external_run_id"] == "run_hermes_123"
        assert metadata.values["canonical_task_id"] == request.task_id
        assert metadata.values["upstream_revision"] == HERMES_PINNED_REVISION

    asyncio.run(scenario())


def test_hermes_orchestrator_disabled_and_http_errors_are_canonical() -> None:
    async def scenario() -> None:
        disabled = HermesOrchestrator(HermesAdapterConfig(enabled=False))
        with pytest.raises(ContractError) as disabled_error:
            await disabled.plan(_plan_request())
        assert disabled_error.value.code is ErrorCode.UNAVAILABLE

        transport = FakeHermesTransport(
            [
                HermesHttpResponse(
                    401,
                    {"error": {"message": "bad bearer token"}},
                )
            ]
        )
        enabled = HermesOrchestrator(
            HermesAdapterConfig(enabled=True),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as auth_error:
            await enabled.plan(_plan_request())
        assert auth_error.value.code is ErrorCode.UNAUTHORIZED
        assert auth_error.value.provider_id == HERMES_ADAPTER_ID
        assert "bad bearer token" in auth_error.value.message

    asyncio.run(scenario())


def test_hermes_invalid_plan_and_approval_pause_fail_closed() -> None:
    async def scenario() -> None:
        invalid_transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_invalid", "status": "started"}),
                HermesHttpResponse(
                    200,
                    {
                        "run_id": "run_invalid",
                        "status": "completed",
                        "output": "not-json",
                    },
                ),
            ]
        )
        invalid = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=invalid_transport,
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as invalid_error:
            await invalid.plan(_plan_request())
        assert invalid_error.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE

        approval_transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_approval", "status": "started"}),
                HermesHttpResponse(
                    200,
                    {
                        "run_id": "run_approval",
                        "status": "waiting_for_approval",
                    },
                ),
            ]
        )
        approval = HermesOrchestrator(
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=approval_transport,
            secret_resolver=lambda _: None,
        )
        with pytest.raises(ContractError) as approval_error:
            await approval.plan(_plan_request())
        assert approval_error.value.code is ErrorCode.FORBIDDEN
        assert "does not delegate approval authority" in approval_error.value.message

    asyncio.run(scenario())


def test_hermes_reconcile_and_cancel_keep_native_id_adapter_private() -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport(
            [
                HermesHttpResponse(200, {"run_id": "run_native", "status": "running"}),
                HermesHttpResponse(200, {"ok": True}),
                HermesHttpResponse(
                    200,
                    {"run_id": "run_native", "status": "cancelled"},
                ),
            ]
        )
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(enabled=True),
            transport=transport,
            secret_resolver=lambda _: None,
        )
        context = OperationContext(correlation_id="corr-native")

        running = await orchestrator.reconcile_external_run("run_native", context)
        cancelled = await orchestrator.cancel_external_run("run_native", context)

        assert running.external_run_id == "run_native"
        assert running.status == "running"
        assert cancelled.status == "cancelled"
        assert transport.calls[1].url.endswith("/v1/runs/run_native/stop")
        assert transport.calls[2].url.endswith("/v1/runs/run_native")

    asyncio.run(scenario())


def test_hermes_agent_mapper_pins_agent_team_model_and_capability_contracts() -> None:
    async def scenario() -> None:
        repository = InMemoryAgentRepository()
        service = AgentService(repository)
        agent = service.create_agent(
            AgentProfile(
                name="Hermes Worker",
                role="implementer",
                description="Maps into Hermes without changing canonical identity.",
                instructions=AgentInstructions(
                    role=InstructionSource(content="Implement the task.", version="7"),
                    platform_constraint_refs=("platform:core",),
                    project_instruction_refs=("workspace:instructions",),
                ),
                model=AgentModelPolicy(),
                capabilities=AgentCapabilityPolicy(
                    allowed=("tool.echo",),
                    constraints=(
                        CapabilityConstraint(
                            capability_id="tool.echo",
                            required=True,
                            exact_version="1.0",
                        ),
                    ),
                ),
            ),
            owner_ref=OWNER,
        )
        team = service.create_team(
            AgentTeamProfile(
                name="Hermes Team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(agent.agent_id, agent.revision),
                        role="implementer",
                        can_delegate_to=(),
                    ),
                ),
                leader_agent_id=agent.agent_id,
                shared_capability_ids=("tool.echo",),
                shared_resource_refs=("memory-config:team",),
                max_parallel_agents=2,
                max_steps=6,
            ),
            owner_ref=OWNER,
        )
        spec = AgentExecutionSpec(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_revision=agent,
            capability_ids=("tool.echo",),
            capability_versions={"tool.echo": "1.0"},
            selected_model_config_id="model-local",
            selected_provider_id="provider-local",
            team_revision=team,
            task_context={"goal": "ship"},
            project_context={"workspace": "repo"},
        )
        mapper = HermesAgentMapper(
            HermesAdapterConfig(
                enabled=True,
                model_bridge={"model-local": "openai-compatible/local-model"},
                capability_bridge={"tool.echo": "echo"},
            )
        )

        mapping = await mapper.map_agent(spec)

        assert mapping.adapter_id == HERMES_ADAPTER_ID
        assert mapping.runtime_ref.startswith(
            f"hermes:mapping:{agent.agent_id}:r{agent.revision}:"
        )
        mapped_agent = mapping.metadata["agent"]
        assert isinstance(mapped_agent, dict)
        assert mapped_agent["canonical_agent_id"] == agent.agent_id
        assert mapped_agent["canonical_revision"] == agent.revision
        mapped_model = mapped_agent["model"]
        assert isinstance(mapped_model, dict)
        assert mapped_model["canonical_model_config_id"] == "model-local"
        assert mapped_model["hermes_model"] == "openai-compatible/local-model"
        capabilities = mapped_agent["capabilities"]
        assert isinstance(capabilities, list)
        assert capabilities == [
            {
                "canonical_capability_id": "tool.echo",
                "canonical_version": "1.0",
                "hermes_tool": "echo",
            }
        ]
        mapped_team = mapping.metadata["team"]
        assert isinstance(mapped_team, dict)
        assert mapped_team["canonical_team_id"] == team.team_id
        assert mapped_team["canonical_revision"] == team.revision
        assert mapped_team["max_parallel_agents"] == 2
        assert mapped_team["max_steps"] == 6
        assert mapped_team["shared_resource_refs"] == ["memory-config:team"]

        reference = await ReferenceOrchestratorMapper().map_agent(spec)
        assert reference.adapter_id != mapping.adapter_id
        assert spec.agent_revision.agent_id == agent.agent_id
        assert spec.agent_revision.revision == agent.revision

    asyncio.run(scenario())


def test_hermes_agent_mapper_fails_closed_for_unmapped_model_or_capability() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        agent = service.create_agent(
            AgentProfile(
                name="Strict bridge",
                role="worker",
                instructions=AgentInstructions(
                    role=InstructionSource(content="Work."),
                ),
            ),
            owner_ref=OWNER,
        )
        spec = AgentExecutionSpec(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_revision=agent,
            capability_ids=("tool.required",),
            capability_versions={"tool.required": "2.0"},
            selected_model_config_id="model-required",
            selected_provider_id="provider",
        )

        missing_model = HermesAgentMapper(
            HermesAdapterConfig(
                enabled=True,
                capability_bridge={"tool.required": "required"},
            )
        )
        with pytest.raises(ContractError) as model_error:
            await missing_model.map_agent(spec)
        assert model_error.value.code is ErrorCode.INVALID_CONFIGURATION

        missing_capability = HermesAgentMapper(
            HermesAdapterConfig(
                enabled=True,
                model_bridge={"model-required": "local/model"},
            )
        )
        with pytest.raises(ContractError) as capability_error:
            await missing_capability.map_agent(spec)
        assert capability_error.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    asyncio.run(scenario())


def test_reference_and_hermes_implement_same_canonical_plan_contract() -> None:
    async def scenario() -> None:
        request = _plan_request()
        reference = await FakeOrchestrator().plan(request)
        transport = FakeHermesTransport(
            [
                HermesHttpResponse(202, {"run_id": "run_equiv", "status": "started"}),
                HermesHttpResponse(
                    200,
                    {
                        "run_id": "run_equiv",
                        "status": "completed",
                        "output": json.dumps(
                            {
                                "summary": "Equivalent",
                                "steps": [
                                    {
                                        "key": "step-1",
                                        "title": "Execute requested work",
                                        "objective": request.objective,
                                        "depends_on": [],
                                    }
                                ],
                            }
                        ),
                    },
                ),
            ]
        )
        hermes = await HermesOrchestrator(
            HermesAdapterConfig(enabled=True, poll_interval_seconds=0.001),
            transport=transport,
            secret_resolver=lambda _: None,
        ).plan(request)

        assert isinstance(reference, PlanResponse)
        assert isinstance(hermes, PlanResponse)
        assert reference.steps[0].key == hermes.steps[0].key == "step-1"
        assert request.task_id.startswith("task_")

    asyncio.run(scenario())


def test_hermes_health_and_profile_prefix_are_configuration_driven() -> None:
    async def scenario() -> None:
        transport = FakeHermesTransport([HermesHttpResponse(200, {"status": "ok"})])
        orchestrator = HermesOrchestrator(
            HermesAdapterConfig(
                enabled=True,
                base_url="http://hermes:8642/",
                profile="research profile",
            ),
            transport=transport,
            secret_resolver=lambda _: None,
        )

        assert await orchestrator.health() is HealthStatus.HEALTHY
        assert (
            transport.calls[0].url
            == "http://hermes:8642/p/research%20profile/health"
        )
        assert orchestrator.descriptor.available is True
        assert HermesOrchestrator(HermesAdapterConfig()).descriptor.available is False

    asyncio.run(scenario())
