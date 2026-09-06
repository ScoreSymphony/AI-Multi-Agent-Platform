from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.security import ActorType, AuthorizationAction, ResourceType

PASSWORD = "correct horse battery staple"


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def test_single_node_routes_profile_management_through_real_authorization(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-443-routing-profile-management",
        )
        project = deployment.scopes.create_project(
            key="issue-443-project",
            name="Issue 443",
            owner_type="user",
            owner_id=admin.user_id,
        )

        assert "model-routing-profiles" in deployment.control_plane.registered_collections
        assert {
            "model-routing-profile:create",
            "model-routing-profile:version",
            "model-routing-profile:enable",
            "model-routing-profile:disable",
        }.issubset(deployment.control_plane.registered_commands)

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile:create",
                headers=_headers(token.secret, key="issue-443-create"),
                body={
                    "resource_ref": "model-routing-profiles",
                    "name": "Research",
                    "description": "Project-scoped routing profile",
                    "project_id": project.id,
                    "policy": {
                        "requirements": {
                            "min_context_window": 8_192,
                            "tool_calling": True,
                        },
                        "fallback": "route",
                    },
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        profile_id = created.body["id"]
        assert isinstance(profile_id, str)
        assert created.body["project_id"] == project.id
        assert created.body["current_revision"] == 1
        revision_one_ref = created.body["current_revision_ref"]
        assert revision_one_ref == f"{profile_id}@r1"

        listed = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/model-routing-profiles",
                headers=_headers(token.secret),
                query={"filter[project_id]": project.id},
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        assert [item["id"] for item in listed.body["items"]] == [profile_id]

        current = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}",
                headers=_headers(token.secret),
            )
        )
        assert current.status == 200
        assert isinstance(current.body, dict)
        assert current.body["current_revision_ref"] == revision_one_ref

        versioned = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile:version",
                headers=_headers(token.secret, key="issue-443-version"),
                body={
                    "resource_ref": profile_id,
                    "expected_revision": 1,
                    "name": "Research v2",
                    "description": "Require structured output",
                    "policy": {
                        "requirements": {
                            "min_context_window": 8_192,
                            "tool_calling": True,
                            "structured_output": True,
                        },
                        "fallback": "route",
                    },
                },
            )
        )
        assert versioned.status == 200
        assert isinstance(versioned.body, dict)
        assert versioned.body["current_revision"] == 2
        assert versioned.body["revision"]["name"] == "Research v2"

        historical = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{revision_one_ref}",
                headers=_headers(token.secret),
            )
        )
        assert historical.status == 200
        assert isinstance(historical.body, dict)
        assert historical.body["id"] == revision_one_ref
        assert historical.body["revision"] == 1
        assert historical.body["name"] == "Research"
        assert historical.body["policy"]["requirements"]["structured_output"] is False

        disabled = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile:disable",
                headers=_headers(token.secret, key="issue-443-disable"),
                body={"resource_ref": profile_id},
            )
        )
        assert disabled.status == 200
        assert isinstance(disabled.body, dict)
        assert disabled.body["enabled"] is False

        enabled = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile:enable",
                headers=_headers(token.secret, key="issue-443-enable"),
                body={"resource_ref": profile_id},
            )
        )
        assert enabled.status == 200
        assert isinstance(enabled.body, dict)
        assert enabled.body["enabled"] is True

        assignment_decision = await deployment.authorization.authorize(
            AuthorizationRequest(
                principal_ref=admin.user_id,
                actor_type=ActorType.HUMAN.value,
                action=AuthorizationAction.MODEL_ROUTING_PROFILE_ASSIGN.value,
                resource_type=ResourceType.MODEL_ROUTING_PROFILE.value,
                resource_ref=f"{profile_id}@r2",
                context=OperationContext(
                    correlation_id="issue-443-assign",
                    owner_type="user",
                    owner_id=admin.user_id,
                    project_id=project.id,
                ),
            )
        )
        assert assignment_decision.outcome is AuthorizationOutcome.ALLOW

        outsider = deployment.authentication.create_local_user("outsider", PASSWORD)
        outsider_token = deployment.authentication.create_personal_access_token(
            outsider.user_id,
            purpose="issue-443-unauthorized",
        )
        denied = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}",
                headers=_headers(outsider_token.secret),
            )
        )
        assert denied.status == 403
        assert isinstance(denied.body, dict)
        assert denied.body["code"] == "forbidden"

        restarted = build_single_node_deployment(config)
        historical_after_restart = await restarted.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{revision_one_ref}",
                headers=_headers(token.secret),
            )
        )
        assert historical_after_restart.status == 200
        assert historical_after_restart.body == historical.body

    asyncio.run(scenario())
