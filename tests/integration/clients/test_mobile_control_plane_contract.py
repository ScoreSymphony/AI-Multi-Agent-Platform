from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, SingleNodeDeployment

ROOT = Path(__file__).resolve().parents[3]
MOBILE_CLIENT = ROOT / "mobile" / "src" / "client.ts"


def _headers(token: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if idempotency_key is not None:
        headers["idempotency-key"] = idempotency_key
    return headers


async def _request(
    deployment: SingleNodeDeployment,
    token: str,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    query: dict[str, str] | None = None,
    idempotency_key: str | None = None,
):
    return await deployment.http.handle(
        HTTPRequest(
            method=method,
            path=path,
            headers=_headers(token, idempotency_key=idempotency_key),
            query=query or {},
            body=body or {},
        )
    )


def test_mobile_routes_project_real_single_node_control_plane_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_default_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "platform",
                secure_cookie=False,
            ),
            enable_distributed_execution=True,
        )
        admin = deployment.bootstrap_admin("mobile-contract-admin", secrets.token_urlsafe(32))
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="mobile-control-plane-contract",
        )
        token = credential.secret

        actor = await _request(deployment, token, "GET", "/api/v1/auth/me")
        assert actor.status == 200
        assert actor.body["actor_type"] == "human"

        created = await _request(
            deployment,
            token,
            "POST",
            "/api/v1/tasks",
            body={
                "title": "Mobile Control Plane contract",
                "objective": "Prove real canonical mobile projections.",
                "owner_type": "user",
                "owner_id": admin.user_id,
            },
            idempotency_key="mobile-contract-task",
        )
        assert created.status == 201
        task_id = created.body["id"]

        collection_paths = (
            "/api/v1/tasks",
            "/api/v1/runs",
            "/api/v1/results",
            "/api/v1/artifacts",
            "/api/v1/approvals",
            "/api/v1/verification-reviews",
            "/api/v1/notifications",
            "/api/v1/agents",
            "/api/v1/workers",
        )
        pages = {}
        for path in collection_paths:
            response = await _request(deployment, token, "GET", path)
            assert response.status == 200, (path, response.body)
            assert isinstance(response.body["items"], list)
            pages[path] = response.body

        assert any(item["id"] == task_id for item in pages["/api/v1/tasks"]["items"])

        search = await _request(
            deployment,
            token,
            "GET",
            "/api/v1/search",
            query={"q": "Mobile Control Plane contract", "limit": "50"},
        )
        assert search.status == 200
        assert isinstance(search.body["items"], list)

        openapi = await _request(deployment, token, "GET", "/api/v1/openapi.json")
        assert openapi.status == 200
        assert "/api/v1/conversation-messages/{message_id}:resume-task" in openapi.body["paths"]

        manifest = await _request(deployment, token, "GET", "/api/v1/")
        assert manifest.status == 200
        resources = set(manifest.body["resources"])
        commands = set(manifest.body.get("commands") or ())
        assert {
            "tasks",
            "runs",
            "results",
            "artifacts",
            "approvals",
            "verification-reviews",
            "notifications",
            "agents",
            "workers",
        } <= resources
        assert {
            "approval.approve",
            "approval.deny",
            "verification.accept",
            "verification.reject",
            "verification.request-changes",
            "notification.mark-read",
        } <= commands

        source = MOBILE_CLIENT.read_text(encoding="utf-8")
        for route_fragment in (
            '"/tasks?limit=50&sort=updated_at&direction=desc"',
            '"/runs?limit=50&sort=updated_at&direction=desc"',
            '"/results?limit=50"',
            '"/artifacts?limit=50"',
            '"/approvals?limit=50&sort=created_at&direction=desc"',
            '"/verification-reviews?limit=50"',
            '"/notifications?limit=50&sort=created_at&direction=desc"',
            '"/agents?limit=50"',
            '"/workers?limit=50"',
        ):
            assert route_fragment in source

        for command in (
            "approval.approve",
            "approval.deny",
            "verification.accept",
            "verification.reject",
            "verification.request-changes",
            "notification.mark-read",
        ):
            assert command in source
        assert "conversation-messages/" in source
        assert ":resume-task" in source

    asyncio.run(scenario())
