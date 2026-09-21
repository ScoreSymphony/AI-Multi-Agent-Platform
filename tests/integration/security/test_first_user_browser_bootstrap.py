from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane.first_user_bootstrap import (
    BOOTSTRAP_STATUS_PATH,
    AuthenticatedControlPlaneHTTP,
)
from ai_multi_agent_platform.control_plane.http import ControlPlaneASGI, HTTPRequest
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


def test_fresh_install_bootstrap_creates_admin_policy_and_authenticated_browser_session(
    tmp_path,
) -> None:
    http, authentication, authorization = _http(tmp_path)

    status = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/auth/bootstrap-status")))
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

    initialized = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/auth/bootstrap-status")))
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

    openapi = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))
    assert openapi.status == 200
    bootstrap_operation = openapi.body["paths"]["/api/v1/auth/bootstrap-admin"]["post"]
    assert bootstrap_operation["responses"][str(repeated.status)] == {
        "$ref": "#/components/responses/Error"
    }
    assert bootstrap_operation["responses"]["405"] == {"$ref": "#/components/responses/Error"}


def test_partial_first_user_bootstrap_can_resume_only_with_existing_credentials(tmp_path) -> None:
    http, authentication, authorization = _http(tmp_path)
    account = authentication.bootstrap_first_admin("alice", PASSWORD)

    partial = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/auth/bootstrap-status")))
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


def test_setup_resources_and_mutations_remain_authenticated_during_bootstrap(tmp_path) -> None:
    http, _authentication, _authorization = _http(tmp_path)

    setup_status = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/setup-sessions/initial-setup",
            )
        )
    )
    provision = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.provision-setup",
                body={"resource_ref": "initial-setup"},
            )
        )
    )

    assert setup_status.status == 401
    assert provision.status == 401


def test_bootstrap_status_route_preserves_405_and_openapi_ownership(tmp_path) -> None:
    http, _authentication, _authorization = _http(tmp_path)
    status_path = f"/api/v1{BOOTSTRAP_STATUS_PATH}"
    headers = {
        "x-request-id": "request-1331-bootstrap",
        "x-correlation-id": "correlation-1331-bootstrap",
    }

    wrong_method = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=status_path,
                headers=headers,
            )
        )
    )
    assert wrong_method.status == 405
    assert wrong_method.body["code"] == "method_not_allowed"
    assert wrong_method.body["request_id"] == "request-1331-bootstrap"
    assert wrong_method.body["correlation_id"] == "correlation-1331-bootstrap"

    unknown = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/bootstrap-private-status",
                headers=headers,
            )
        )
    )
    assert unknown.status == 404
    assert unknown.body["code"] == "not_found"

    openapi = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))
    assert openapi.status == 200
    paths = openapi.body["paths"]
    assert [path for path in paths if path.endswith(BOOTSTRAP_STATUS_PATH)] == [status_path]

    status_route = paths[status_path]
    assert set(status_route) == {"get"}
    operation = status_route["get"]
    assert operation["security"] == []
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FirstUserBootstrapStatus"
    }
    assert operation["responses"]["405"] == {"$ref": "#/components/responses/Error"}
    assert operation["responses"]["500"] == {"$ref": "#/components/responses/Error"}

    assert openapi.body["components"]["schemas"]["FirstUserBootstrapStatus"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["state", "bootstrap_available", "password_policy"],
        "properties": {
            "state": {
                "type": "string",
                "enum": ["uninitialized", "incomplete", "initialized"],
            },
            "bootstrap_available": {"type": "boolean"},
            "password_policy": {
                "type": "object",
                "additionalProperties": False,
                "required": ["min_length", "max_bytes"],
                "properties": {
                    "min_length": {"type": "integer", "minimum": 1},
                    "max_bytes": {"type": "integer", "minimum": 1},
                },
            },
        },
    }
    assert {path for path in paths if "/auth/bootstrap" in path} == {
        "/api/v1/auth/bootstrap-admin",
        status_path,
    }


def test_bootstrap_status_asgi_pre_body_classification_returns_405(tmp_path) -> None:
    http, _authentication, _authorization = _http(tmp_path)
    app = ControlPlaneASGI(http)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"{", "more_body": False}

    async def send(message):
        messages.append(message)

    _run(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/bootstrap-status",
                "query_string": b"",
                "headers": [
                    (b"x-request-id", b"request-1330-bootstrap-asgi"),
                    (b"x-correlation-id", b"correlation-1330-bootstrap-asgi"),
                    (b"content-type", b"application/json"),
                ],
            },
            receive,
            send,
        )
    )

    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 405
