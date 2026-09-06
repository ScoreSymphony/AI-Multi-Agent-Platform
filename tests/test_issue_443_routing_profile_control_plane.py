from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfilePolicy,
)

_PASSWORD = "correct horse battery staple"


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def test_single_node_exposes_authorized_routing_profile_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-443-routing-profile-management",
        )
        project = deployment.scopes.create_project(
            key="issue-443-project",
            name="Routing profile management",
            owner_type="user",
            owner_id=admin.user_id,
        )

        assert "model-routing-profiles" in deployment.control_plane.registered_collections
        assert {
            "model-routing-profile.create",
            "model-routing-profile.version",
            "model-routing-profile.enable",
            "model-routing-profile.disable",
        }.issubset(deployment.control_plane.registered_commands)

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.create",
                headers=_headers(token.secret, key="issue-443-create"),
                body={
                    "resource_ref": "model-routing-profiles",
                    "name": "Research routing",
                    "description": "Durable profile managed through the standard Control Plane.",
                    "project_id": project.id,
                    "policy": {
                        "requirements": {
                            "min_context_window": 32768,
                            "tool_calling": True,
                            "local_only": False,
                            "self_hosted_only": False,
                        },
                        "preferred_model_ids": [],
                        "fallback": "route",
                    },
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        profile_id = created.body["profile_id"]
        assert isinstance(profile_id, str)
        assert created.body["current_revision"] == 1
        assert created.body["enabled"] is True
        assert created.body["project_id"] == project.id
        assert created.body["exact_ref"] == f"{profile_id}@r1"

        listed = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/model-routing-profiles",
                headers=_headers(token.secret),
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        assert any(item["profile_id"] == profile_id for item in listed.body["items"])

        current = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}",
                headers=_headers(token.secret),
            )
        )
        assert current.status == 200
        assert current.body["exact_ref"] == f"{profile_id}@r1"

        versioned = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.version",
                headers=_headers(token.secret, key="issue-443-version"),
                body={
                    "resource_ref": profile_id,
                    "expected_revision": 1,
                    "name": "Research routing v2",
                    "description": "Second immutable policy revision.",
                    "policy": {
                        "requirements": {
                            "min_context_window": 65536,
                            "tool_calling": True,
                        },
                        "fallback": "route",
                    },
                },
            )
        )
        assert versioned.status == 200
        assert versioned.body["current_revision"] == 2
        assert versioned.body["exact_ref"] == f"{profile_id}@r2"

        exact_v1 = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}@r1",
                headers=_headers(token.secret),
            )
        )
        assert exact_v1.status == 200
        assert exact_v1.body["id"] == f"{profile_id}@r1"
        assert exact_v1.body["revision"]["name"] == "Research routing"

        disabled = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.disable",
                headers=_headers(token.secret, key="issue-443-disable"),
                body={"resource_ref": profile_id},
            )
        )
        assert disabled.status == 200
        assert disabled.body["enabled"] is False

        enabled = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.enable",
                headers=_headers(token.secret, key="issue-443-enable"),
                body={"resource_ref": profile_id},
            )
        )
        assert enabled.status == 200
        assert enabled.body["enabled"] is True

        restarted = build_single_node_deployment(config)
        restored = restarted.routing_profile_repository.get_definition(profile_id)
        assert restored.current_revision == 2
        assert restored.enabled is True
        assert restarted.routing_profile_repository.list_revisions(profile_id)[0].name == (
            "Research routing"
        )

    asyncio.run(scenario())


def test_real_local_authorization_accepts_management_and_assignment_vocabulary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        operation = OperationContext(
            correlation_id="issue-443-direct-authorization",
            owner_type=owner.type,
            owner_id=owner.id,
        )
        profile = await deployment.routing_profiles.create_profile(
            name="Direct authorization vocabulary",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=owner,
            principal_ref=admin.user_id,
            context=operation,
        )
        versioned = await deployment.routing_profiles.version_profile(
            profile.profile_id,
            name="Direct authorization vocabulary v2",
            policy=ModelRoutingProfilePolicy(),
            principal_ref=admin.user_id,
            context=operation,
            expected_revision=1,
        )
        await deployment.routing_profiles.get_revision(
            versioned.ref,
            principal_ref=admin.user_id,
            context=operation,
        )
        await deployment.routing_profiles.set_enabled(
            profile.profile_id,
            False,
            principal_ref=admin.user_id,
            context=operation,
        )
        await deployment.routing_profiles.set_enabled(
            profile.profile_id,
            True,
            principal_ref=admin.user_id,
            context=operation,
        )
        visible = await deployment.routing_profiles.list_profiles(
            principal_ref=admin.user_id,
            context=operation,
        )
        assert any(item.profile_id == profile.profile_id for item in visible)

        gate = ModelRoutingProfileAssignmentGate(
            deployment.routing_profile_repository,
            authorization=deployment.authorization,
        )
        assigned = await gate.authorize(
            versioned.ref,
            principal_ref=admin.user_id,
            context=operation,
            actor_type="human",
        )
        assert assigned.ref == versioned.ref

    asyncio.run(scenario())
