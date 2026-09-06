from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import (
    SingleNodeConfig,
    build_single_node_deployment,
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


def _canonical_payload(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_exact_revision_is_immutable_and_unauthorized_reads_fail_closed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        admin_token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-443-immutable-revision-followup",
        )
        project = deployment.scopes.create_project(
            key="issue-443-immutable-project",
            name="Immutable routing profile revision",
            owner_type="user",
            owner_id=admin.user_id,
        )

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.create",
                headers=_headers(admin_token.secret, key="issue-443-followup-create"),
                body={
                    "resource_ref": "model-routing-profiles",
                    "name": "Immutable revision v1",
                    "description": "Historical API payload must remain reproducible.",
                    "project_id": project.id,
                    "policy": {
                        "requirements": {"min_context_window": 32768},
                        "fallback": "route",
                    },
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        profile_id = created.body["profile_id"]
        assert isinstance(profile_id, str)

        versioned = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.version",
                headers=_headers(admin_token.secret, key="issue-443-followup-version"),
                body={
                    "resource_ref": profile_id,
                    "expected_revision": 1,
                    "name": "Immutable revision v2",
                    "description": "Current state may evolve independently.",
                    "policy": {
                        "requirements": {"min_context_window": 65536},
                        "fallback": "route",
                    },
                },
            )
        )
        assert versioned.status == 200

        exact_before = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}@r1",
                headers=_headers(admin_token.secret),
            )
        )
        assert exact_before.status == 200
        assert isinstance(exact_before.body, dict)
        assert exact_before.body["id"] == f"{profile_id}@r1"
        assert exact_before.body["exact_ref"] == f"{profile_id}@r1"
        assert exact_before.body["revision"]["revision"] == 1
        assert exact_before.body["revision"]["name"] == "Immutable revision v1"
        for mutable_definition_field in (
            "current_revision",
            "enabled",
            "created_at",
            "updated_at",
        ):
            assert mutable_definition_field not in exact_before.body
        expected_payload = _canonical_payload(exact_before.body)

        unauthorized = deployment.authentication.create_local_user(
            "unauthorized-reader",
            _PASSWORD,
        )
        unauthorized_token = deployment.authentication.create_personal_access_token(
            unauthorized.user_id,
            purpose="issue-443-no-policy",
        )
        forbidden = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}",
                headers=_headers(unauthorized_token.secret),
            )
        )
        assert forbidden.status == 403

        disabled = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.disable",
                headers=_headers(admin_token.secret, key="issue-443-followup-disable"),
                body={"resource_ref": profile_id},
            )
        )
        assert disabled.status == 200
        assert disabled.body["enabled"] is False

        enabled = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/model-routing-profile.enable",
                headers=_headers(admin_token.secret, key="issue-443-followup-enable"),
                body={"resource_ref": profile_id},
            )
        )
        assert enabled.status == 200
        assert enabled.body["enabled"] is True

        exact_after_lifecycle = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}@r1",
                headers=_headers(admin_token.secret),
            )
        )
        assert exact_after_lifecycle.status == 200
        assert _canonical_payload(exact_after_lifecycle.body) == expected_payload

        stable = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}",
                headers=_headers(admin_token.secret),
            )
        )
        assert stable.status == 200
        assert stable.body["current_revision"] == 2
        assert stable.body["enabled"] is True
        assert "updated_at" in stable.body

        restarted = build_single_node_deployment(config)
        exact_after_restart = await restarted.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/model-routing-profiles/{profile_id}@r1",
                headers=_headers(admin_token.secret),
            )
        )
        assert exact_after_restart.status == 200
        assert _canonical_payload(exact_after_restart.body) == expected_payload

    asyncio.run(scenario())
