from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment

PASSWORD = "correct horse battery staple"


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPRequest:
    return HTTPRequest(
        method=method,
        path=path,
        body=body or {},
        headers=headers or {},
    )


def _cookie(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0]


def test_browser_first_run_bootstrap_creates_admin_session_and_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)

        status = await first.http.handle(
            _request("GET", "/api/v1/auth/bootstrap-status")
        )
        assert status.status == 200
        assert status.body == {
            "state": "uninitialized",
            "bootstrap_available": True,
            "password_policy": {
                "minimum_length": 12,
                "maximum_bytes": 1024,
            },
        }

        mismatch = await first.http.handle(
            _request(
                "POST",
                "/api/v1/auth/bootstrap-admin",
                body={
                    "username": "admin",
                    "password": PASSWORD,
                    "password_confirmation": "different password",
                },
            )
        )
        assert mismatch.status == 400
        assert not first.authentication.store.users

        bootstrap = await first.http.handle(
            _request(
                "POST",
                "/api/v1/auth/bootstrap-admin",
                body={
                    "username": "admin",
                    "password": PASSWORD,
                    "password_confirmation": PASSWORD,
                },
            )
        )
        assert bootstrap.status == 201
        assert bootstrap.body["authorization_granted"] is True
        assert bootstrap.body["actor"]["authentication_method"] == "browser_session"
        assert first.authorization.has_policy(bootstrap.body["actor"]["actor_id"])
        assert "HttpOnly" in bootstrap.headers["set-cookie"]
        assert "SameSite=Lax" in bootstrap.headers["set-cookie"]

        cookie = _cookie(bootstrap.headers["set-cookie"])
        me = await first.http.handle(
            _request(
                "GET",
                "/api/v1/auth/me",
                headers={"cookie": cookie},
            )
        )
        assert me.status == 200
        assert me.body["actor_id"] == bootstrap.body["actor"]["actor_id"]

        second_bootstrap = await first.http.handle(
            _request(
                "POST",
                "/api/v1/auth/bootstrap-admin",
                body={
                    "username": "second-admin",
                    "password": PASSWORD,
                    "password_confirmation": PASSWORD,
                },
            )
        )
        assert second_bootstrap.status == 409
        assert second_bootstrap.body["code"] == "bootstrap_unavailable"
        assert len(first.authentication.store.users) == 1

        restarted = build_single_node_deployment(config)
        restarted_status = await restarted.http.handle(
            _request("GET", "/api/v1/auth/bootstrap-status")
        )
        assert restarted_status.status == 200
        assert restarted_status.body["state"] == "initialized"
        assert restarted_status.body["bootstrap_available"] is False

        restarted_me = await restarted.http.handle(
            _request(
                "GET",
                "/api/v1/auth/me",
                headers={"cookie": cookie},
            )
        )
        assert restarted_me.status == 200
        assert restarted_me.body["actor_id"] == bootstrap.body["actor"]["actor_id"]

        # The existing operator path remains a recovery-compatible alternative and
        # observes the same account/policy instead of creating a parallel identity.
        recovered = restarted.bootstrap_admin("admin", PASSWORD)
        assert recovered.user_id == bootstrap.body["actor"]["actor_id"]
        assert restarted.authorization.has_policy(recovered.user_id)

    asyncio.run(scenario())


def test_concurrent_browser_bootstrap_allows_exactly_one_first_user(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "race", secure_cookie=False)
        )

        async def bootstrap(username: str):
            return await deployment.http.handle(
                _request(
                    "POST",
                    "/api/v1/auth/bootstrap-admin",
                    body={
                        "username": username,
                        "password": PASSWORD,
                        "password_confirmation": PASSWORD,
                    },
                )
            )

        first, second = await asyncio.gather(
            bootstrap("admin-a"),
            bootstrap("admin-b"),
        )
        assert sorted((first.status, second.status)) == [201, 409]
        assert len(deployment.authentication.store.users) == 1
        account = next(iter(deployment.authentication.store.users.values()))
        assert deployment.authorization.has_policy(account.user_id)

    asyncio.run(scenario())
