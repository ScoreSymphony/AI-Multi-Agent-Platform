from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    register_agent_control_plane,
)
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-agent-33",
        "X-Correlation-Id": "correlation-agent-33",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "actor-owner",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _profile(name: str, *, role: str = "worker") -> dict[str, object]:
    return {
        "name": name,
        "role": role,
        "instructions": {
            "role": {
                "content": f"Act as {role}.",
                "version": "1",
            }
        },
    }


def _stack() -> tuple[ControlPlane, ControlPlaneHTTP, AgentService]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
    )
    agent_repository = InMemoryAgentRepository()
    service = AgentService(agent_repository)
    runtime = AgentRuntime(service)
    register_agent_control_plane(control_plane, service, runtime=runtime)
    return control_plane, ControlPlaneHTTP(control_plane), service


def test_agent_control_plane_create_update_read_and_start_preserve_revision_truth() -> None:
    async def scenario() -> None:
        control_plane, http, service = _stack()
        assert control_plane.registered_collections == (
            "agent-runs",
            "agent-teams",
            "agents",
            "automation-deliveries",
            "automations",
        )
        assert "agent.create" in control_plane.registered_commands
        assert "agent.start" in control_plane.registered_commands

        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/agent.create",
                headers=_headers("agent-create-1"),
                body={
                    "resource_ref": "agents",
                    "owner_ref": {
                        "type": "organization",
                        "id": "canonical-owner",
                    },
                    "profile": _profile("Control Plane Agent"),
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        agent_id = created.body["id"]
        assert isinstance(agent_id, str)
        assert created.body["current_revision"] == 1
        assert created.body["owner_ref"] == {
            "type": "organization",
            "id": "canonical-owner",
        }

        updated = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/agent.update",
                headers=_headers("agent-update-1"),
                body={
                    "resource_ref": agent_id,
                    "expected_revision": 1,
                    "profile": _profile("Control Plane Agent v2"),
                },
            )
        )
        assert updated.status == 200
        assert isinstance(updated.body, dict)
        assert updated.body["current_revision"] == 2
        assert updated.body["owner_ref"] == {
            "type": "organization",
            "id": "canonical-owner",
        }

        task_id = new_id("task")
        run_id = new_id("run")
        started = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/agent.start",
                headers=_headers("agent-start-1"),
                body={
                    "resource_ref": agent_id,
                    "task_id": task_id,
                    "run_id": run_id,
                    "revision": 2,
                },
            )
        )
        assert started.status == 200
        assert isinstance(started.body, dict)
        agent_run_id = started.body["id"]
        assert isinstance(agent_run_id, str)
        assert started.body["agent"] == {
            "agent_id": agent_id,
            "revision": 2,
        }

        service.update_agent(
            agent_id,
            service.get_agent_revision(agent_id, 2).profile,
            expected_revision=2,
        )
        loaded_run = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/agent-runs/{agent_run_id}",
                headers=_headers(),
            )
        )
        assert loaded_run.status == 200
        assert isinstance(loaded_run.body, dict)
        assert loaded_run.body["agent"] == {
            "agent_id": agent_id,
            "revision": 2,
        }

        loaded_agent = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/agents/{agent_id}",
                headers=_headers(),
            )
        )
        assert loaded_agent.status == 200
        assert isinstance(loaded_agent.body, dict)
        assert loaded_agent.body["current_revision"] == 3

    asyncio.run(scenario())


def test_agent_team_control_plane_uses_exact_member_revisions() -> None:
    async def scenario() -> None:
        _, http, _ = _stack()
        member_ids: list[str] = []
        for index in range(2):
            created = await http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/commands/agent.create",
                    headers=_headers(f"agent-create-team-{index}"),
                    body={
                        "resource_ref": "agents",
                        "profile": _profile(f"Member {index}"),
                    },
                )
            )
            assert created.status == 200
            assert isinstance(created.body, dict)
            member_id = created.body["id"]
            assert isinstance(member_id, str)
            member_ids.append(member_id)

        team = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/agent-team.create",
                headers=_headers("team-create-1"),
                body={
                    "resource_ref": "agent-teams",
                    "profile": {
                        "name": "Control Plane Team",
                        "leader_agent_id": member_ids[0],
                        "members": [
                            {
                                "agent": {
                                    "agent_id": member_ids[0],
                                    "revision": 1,
                                },
                                "role": "implementer",
                            },
                            {
                                "agent": {
                                    "agent_id": member_ids[1],
                                    "revision": 1,
                                },
                                "role": "reviewer",
                            },
                        ],
                    },
                },
            )
        )
        assert team.status == 200
        assert isinstance(team.body, dict)
        team_id = team.body["id"]
        assert isinstance(team_id, str)

        started = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/agent-team.start",
                headers=_headers("team-start-1"),
                body={
                    "resource_ref": team_id,
                    "task_id": new_id("task"),
                    "run_id": new_id("run"),
                    "revision": 1,
                },
            )
        )
        assert started.status == 200
        assert isinstance(started.body, dict)
        agent_runs = started.body["agent_runs"]
        assert isinstance(agent_runs, list)
        assert len(agent_runs) == 2
        assert {
            item["agent"]["agent_id"]
            for item in agent_runs
            if isinstance(item, dict) and isinstance(item.get("agent"), dict)
        } == set(member_ids)
        assert all(
            isinstance(item, dict)
            and isinstance(item.get("agent"), dict)
            and item["agent"]["revision"] == 1
            for item in agent_runs
        )

    asyncio.run(scenario())


def test_agent_extension_is_reflected_in_manifest_and_openapi() -> None:
    async def scenario() -> None:
        _, http, _ = _stack()
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        assert isinstance(resources, list)
        for resource in ("agents", "agent-teams", "agent-runs"):
            assert resource in resources

        openapi = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert "/api/v1/agents" in paths
        assert "/api/v1/agent-teams" in paths
        assert "/api/v1/agent-runs" in paths
        assert "/api/v1/commands/{command}" in paths

    asyncio.run(scenario())
