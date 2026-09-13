from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.capabilities import ECHO_CAPABILITY_ID, NativeEchoProvider
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef

PASSWORD = "correct horse battery staple"


def _profile() -> AgentProfile:
    return AgentProfile(
        name="Single-node configurable Agent",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use only explicitly assigned capabilities."),
        ),
    )


def _context(actor: ActorContext, request_id: str) -> RequestContext:
    return RequestContext(
        request_id=request_id,
        correlation_id="issue-696",
        actor=actor,
        idempotency_key=request_id,
    )


def test_single_node_capability_assignment_create_and_revise_are_canonical(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        actor = ActorContext(
            principal_ref=admin.user_id,
            owner_type="user",
            owner_id=admin.user_id,
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        agent = deployment.agents.create_agent(_profile(), owner_ref=owner)
        await deployment.capabilities.register_provider(NativeEchoProvider())

        assert "capability-assignment.create" in deployment.control_plane.registered_commands
        assert "capability-assignment.revise" in deployment.control_plane.registered_commands

        created = await deployment.control_plane.execute_command(
            _context(actor, "issue-696-create"),
            "capability-assignment.create",
            "capability-assignments",
            {
                "content": {
                    "target": {
                        "subject_type": "agent",
                        "subject_id": agent.agent_id,
                    },
                    "required": [],
                    "allowed": [
                        {
                            "capability_id": ECHO_CAPABILITY_ID,
                            "exact_version": None,
                            "compatibility": None,
                            "privileged": False,
                            "approval_required": False,
                        }
                    ],
                    "denied": [],
                    # Deliberately untrusted browser provenance. The Control Plane must replace it.
                    "provenance": {
                        "source": "spoofed-browser",
                        "creator_ref": "other-user",
                    },
                }
            },
        )

        assignment_id = created["id"]
        assert isinstance(assignment_id, str)
        assert created["current_revision"] == 1
        revision = created["revision"]
        assert isinstance(revision, dict)
        content = revision["content"]
        assert isinstance(content, dict)
        assert content["provenance"] == {
            "source": "control-plane",
            "creator_ref": admin.user_id,
        }

        revised = await deployment.control_plane.execute_command(
            _context(actor, "issue-696-revise"),
            "capability-assignment.revise",
            assignment_id,
            {
                "expected_revision": 1,
                "content": {
                    "target": {
                        "subject_type": "agent",
                        "subject_id": agent.agent_id,
                    },
                    "required": [
                        {
                            "capability_id": ECHO_CAPABILITY_ID,
                            "exact_version": None,
                            "compatibility": None,
                            "privileged": False,
                            "approval_required": False,
                        }
                    ],
                    "allowed": [],
                    "denied": [],
                },
            },
        )
        assert revised["current_revision"] == 2
        revised_content = revised["revision"]["content"]
        assert isinstance(revised_content, dict)
        assert revised_content["required"][0]["capability_id"] == ECHO_CAPABILITY_ID

    asyncio.run(scenario())
