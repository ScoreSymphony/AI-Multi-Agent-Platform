from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane.first_user_bootstrap import AuthenticatedControlPlaneHTTP
from ai_multi_agent_platform.control_plane.http import HTTPRequest
from ai_multi_agent_platform.security import LocalAuthenticationService, ScryptPasswordHasher
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider

PASSWORD = "correct horse battery staple"


class _ControlPlaneStub:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()


def _http(tmp_path):
    authentication = LocalAuthenticationService(
        store=SqliteAuthenticationStore(tmp_path / "authentication.sqlite3"),
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
    )
    authorization = SqliteLocalAuthorizationProvider(tmp_path / "authorization.sqlite3")
    http = AuthenticatedControlPlaneHTTP(
        _ControlPlaneStub(),
        authentication,
        authorization=authorization,
        secure_cookie=False,
    )
    return http, authentication, authorization


def _run(awaitable):
    return asyncio.run(awaitable)


def test_fresh_install_bootstrap_creates_admin_policy_and_authenticated_browser_session(tmp_path) -> None:
    http, authentication, authorization = _http(tmp_path)

    status = _run(
        http.handle(HTTPRequest(method="GET", path="/api/v1/auth/bootstrap-status"))
    )
    assert status.status == 200
    assert status.body == {
        "state": "uninitialized",
        "bootstrap_available": True,
        "password_policy": {"min_length": 12, "max_bytes": 1024},
    }

    bootstrap = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/bootstrap-admin",
                body={"username": "alice", "password": PASSWORD},
            )
        )
    )
    assert bootstrap.status == 201
    assert bootstrap.body["authorization_granted"] is True
    assert bootstrap.body["actor"]["actor_type"] == "human"
    assert bootstrap.body["actor"]["authentication_method"] == "browser_session"
    assert "HttpOnly" in bootstrap.headers["set-cookie"]
    assert "SameSite=Lax" in bootstrap.headers["set-cookie"]

    user_id = bootstrap.body["actor"]["actor_id"]
    assert len(authentication.store.users) == 1
    assert authorization.has_policy(user_id)

    cookie = bootstrap.headers["set-cookie"].split(";", 1)[0]
    me = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"cookie": cookie},
            )
        )
    )
    assert me.status == 200
    assert me.body["actor_id"] == user_id

    initialized = _run(
        http.handle(HTTPRequest(method="GET", path="/api/v1/auth/bootstrap-status"))
    )
    assert initialized.body["state"] == "initialized"
    assert initialized.body["bootstrap_available"] is False

    repeated = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/bootstrap-admin",
                body={"username": "mallory", "password": "another sufficiently long password"},
            )
        )
    )
    assert repeated.status == 409
    assert repeated.body["code"] == "bootstrap_unavailable"
    assert len(authentication.store.users) == 1


def test_partial_first_user_bootstrap_can_resume_only_with_existing_credentials(tmp_path) -> None:
    http, authentication, authorization = _http(tmp_path)
    account = authentication.bootstrap_first_admin("alice", PASSWORD)

    partial = _run(
        http.handle(HTTPRequest(method="GET", path="/api/v1/auth/bootstrap-status"))
    )
    assert partial.body["state"] == "incomplete"
    assert partial.body["bootstrap_available"] is True

    rejected = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/bootstrap-admin",
                body={"username": "mallory", "password": "another sufficiently long password"},
            )
        )
    )
    assert rejected.status == 401
    assert not authorization.has_policy(account.user_id)

    resumed = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/bootstrap-admin",
                body={"username": "alice", "password": PASSWORD},
            )
        )
    )
    assert resumed.status == 201
    assert resumed.body["actor"]["actor_id"] == account.user_id
    assert authorization.has_policy(account.user_id)


def test_parallel_first_user_attempts_allow_exactly_one_success(tmp_path) -> None:
    http, authentication, authorization = _http(tmp_path)

    async def attempt(username: str, password: str):
        return await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/bootstrap-admin",
                body={"username": username, "password": password},
            )
        )

    async def race():
        return await asyncio.gather(
            attempt("alice", PASSWORD),
            attempt("bob", "another correct horse battery staple"),
        )

    first, second = _run(race())
    assert sorted((first.status, second.status)) == [201, 409]
    assert len(authentication.store.users) == 1
    account = next(iter(authentication.store.users.values()))
    assert authorization.has_policy(account.user_id)
