from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentDataAccess,
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
    register_agent_control_plane,
)
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import MemoryScope, new_knowledge_source_id
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class RecordingTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del timeout
        parsed = urlsplit(url)
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        self.calls.append((method, parsed.path))
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _http(*, agents: AgentService | None = None) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=repository)
    if agents is not None:
        register_agent_control_plane(control_plane, agents)
    return ControlPlaneHTTP(control_plane)


def _invoke(
    config: Path,
    transport: RecordingTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def _seed_agents() -> tuple[AgentService, str, str, str]:
    service = AgentService(InMemoryAgentRepository())
    knowledge_source_id = new_knowledge_source_id()
    agent = service.create_agent(
        AgentProfile(
            name="CLI Analyst",
            role="analysis",
            instructions=AgentInstructions(
                role=InstructionSource(content="Inspect canonical state only."),
            ),
            model=AgentModelPolicy(
                requirements=RoutingRequirements(explicit_model_id="model_cli_test"),
            ),
            capabilities=AgentCapabilityPolicy(
                allowed=("tool.echo",),
                constraints=(CapabilityConstraint(capability_id="tool.echo"),),
            ),
            data_access=AgentDataAccess(
                memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
                memory_config_refs=("memory.default",),
                knowledge_source_ids=(knowledge_source_id,),
            ),
            resource_hints={"cpu": "small"},
            metadata={"purpose": "cli-acceptance"},
        ),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    team = service.create_team(
        AgentTeamProfile(
            name="CLI Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(agent_id=agent.agent_id, revision=agent.revision),
                    role="analyst",
                ),
            ),
            shared_capability_ids=("tool.echo",),
            max_parallel_agents=1,
            metadata={"purpose": "cli-acceptance"},
        ),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    return service, agent.agent_id, team.team_id, knowledge_source_id


def test_agent_cli_reads_canonical_agent_team_and_run_resources(tmp_path: Path) -> None:
    service, agent_id, team_id, knowledge_source_id = _seed_agents()
    transport = RecordingTransport(_http(agents=service))
    config = tmp_path / "cli.json"

    code, agents, error = _invoke(config, transport, "agent", "list")
    assert code == 0 and not error
    assert [item["id"] for item in _items(agents)] == [agent_id]

    code, agent, error = _invoke(config, transport, "agent", "show", agent_id)
    assert code == 0 and not error
    agent_data = agent["data"]
    assert agent_data["current_revision"] == 1
    profile = agent_data["revision"]["profile"]
    assert profile["enabled"] is True
    assert profile["model"]["requirements"]["explicit_model_id"] == "model_cli_test"
    assert profile["capabilities"]["allowed"] == ["tool.echo"]
    assert profile["capabilities"]["constraints"][0]["capability_id"] == "tool.echo"
    assert profile["data_access"]["memory_scopes"] == ["task", "workspace"]
    assert profile["data_access"]["knowledge_source_ids"] == [knowledge_source_id]
    assert profile["metadata"]["purpose"] == "cli-acceptance"

    code, teams, error = _invoke(config, transport, "agent-team", "list")
    assert code == 0 and not error
    assert [item["id"] for item in _items(teams)] == [team_id]

    code, team, error = _invoke(config, transport, "agent-team", "show", team_id)
    assert code == 0 and not error
    team_profile = team["data"]["revision"]["profile"]
    assert team_profile["members"][0]["agent"] == {"agent_id": agent_id, "revision": 1}
    assert team_profile["shared_capability_ids"] == ["tool.echo"]
    assert team_profile["enabled"] is True

    code, runs, error = _invoke(config, transport, "agent-run", "list")
    assert code == 0 and not error
    assert _items(runs) == []

    assert transport.calls == [
        ("GET", "/api/v1/agents"),
        ("GET", f"/api/v1/agents/{agent_id}"),
        ("GET", "/api/v1/agent-teams"),
        ("GET", f"/api/v1/agent-teams/{team_id}"),
        ("GET", "/api/v1/agent-runs"),
    ]


def test_agent_cli_has_no_backend_fallback_when_agent_api_is_absent(tmp_path: Path) -> None:
    transport = RecordingTransport(_http())
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(config, transport, "agent", "list")

    assert code == 3
    assert payload == {}
    assert '"code":"not_found"' in error
    assert transport.calls == [("GET", "/api/v1/agents")]
