from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security import LocalAuthenticationService, ScryptPasswordHasher

NOW = datetime(2026, 9, 20, 20, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"


class _PairingControlPlane:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        **_: object,
    ) -> None:
        del context, action, resource_ref

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        del context
        return {"items": [], "next_cursor": None, "total": 0, "limit": query.limit}


def _auth() -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_public_pairing_exchange_and_server_side_device_revocation() -> None:
    auth = _auth()
    admin = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    operator = auth.create_personal_access_token(admin.user_id, purpose="pairing-test", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_PairingControlPlane(), auth, secure_cookie=False)
    operator_headers = {"authorization": f"Bearer {operator.secret}"}

    created = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/mobile-pairings",
                headers=operator_headers,
                body={"server_origin": "https://platform.example"},
            )
        )
    )
    assert created.status == 201
    pairing_code = created.body["pairing_code"]
    pairing_id = created.body["id"]
    assert created.body["qr_payload"].startswith("aiagentplatform://pair?")
    assert "amp1." not in created.body["qr_payload"]

    consumed = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/mobile-pairings:consume",
                headers={},
                body={
                    "pairing_code": pairing_code,
                    "pairing_id": pairing_id,
                    "server_origin": "https://platform.example",
                    "device_name": "Pixel",
                    "device_platform": "android",
                    "protocol_version": 1,
                },
            )
        )
    )
    assert consumed.status == 201
    mobile_secret = consumed.body["credential"]["secret"]
    device_id = consumed.body["device"]["id"]
    assert consumed.body["credential"]["secret_display"] == "one_time"

    replay = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/mobile-pairings:consume",
                headers={},
                body={
                    "pairing_code": pairing_code,
                    "pairing_id": pairing_id,
                    "server_origin": "https://platform.example",
                    "device_name": "Replay",
                    "device_platform": "android",
                },
            )
        )
    )
    assert replay.status == 401

    me = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"authorization": f"Bearer {mobile_secret}"},
            )
        )
    )
    assert me.status == 200
    assert me.body["authentication_method"] == "mobile_token"

    listed = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/mobile-devices",
                headers=operator_headers,
            )
        )
    )
    assert listed.status == 200
    assert listed.body["items"][0]["id"] == device_id
    assert "secret" not in repr(listed.body).casefold()

    revoked = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-devices/{device_id}:revoke",
                headers=operator_headers,
            )
        )
    )
    assert revoked.status == 200

    rejected = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"authorization": f"Bearer {mobile_secret}"},
            )
        )
    )
    assert rejected.status == 401


def test_mobile_pairing_openapi_marks_only_exchange_public() -> None:
    auth = _auth()
    http = AuthenticatedControlPlaneHTTP(_PairingControlPlane(), auth, secure_cookie=False)
    response = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/openapi.json",
                headers={},
            )
        )
    )
    assert response.status == 200
    paths = response.body["paths"]
    consume = paths["/api/v1/auth/mobile-pairings:consume"]["post"]
    create = paths["/api/v1/auth/mobile-pairings"]["post"]
    assert consume["security"] == []
    assert create["security"] != []
    assert "/api/v1/auth/mobile-devices/{device_id}:revoke" in paths
