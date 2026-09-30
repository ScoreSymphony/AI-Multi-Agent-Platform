from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.frontend_preferences import (
    FRONTEND_PREFERENCE_COLLECTION,
    FRONTEND_PREFERENCE_COMMANDS,
    FrontendPreferenceService,
    InMemoryFrontendPreferenceRepository,
    SqliteFrontendPreferenceRepository,
    register_frontend_preference_control_plane,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-frontend-preference",
        "X-Correlation-Id": "correlation-frontend-preference",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "test",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _control_plane() -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(),
    )
    register_frontend_preference_control_plane(
        control_plane,
        FrontendPreferenceService(InMemoryFrontendPreferenceRepository()),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def test_sqlite_frontend_preference_survives_service_restart(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "frontend-preferences.sqlite3"
        first = FrontendPreferenceService(SqliteFrontendPreferenceRepository(database))
        saved = await first.update(
            "user:test",
            {"version": 1, "appearance": {"accent": "#112233"}},
            expected_revision=0,
        )
        assert saved.revision == 1

        second = FrontendPreferenceService(SqliteFrontendPreferenceRepository(database))
        restored = await second.get("user:test")
        assert restored.revision == 1
        assert restored.customization == {
            "version": 1,
            "appearance": {"accent": "#112233"},
        }

    asyncio.run(scenario())


def test_frontend_preference_revision_conflict_is_fail_closed() -> None:
    async def scenario() -> None:
        service = FrontendPreferenceService(InMemoryFrontendPreferenceRepository())
        await service.update(
            "user:test",
            {"version": 1, "appearance": {"accent": "#112233"}},
            expected_revision=0,
        )
        with pytest.raises(ContractError) as failure:
            await service.update(
                "user:test",
                {"version": 1, "appearance": {"accent": "#445566"}},
                expected_revision=0,
            )
        assert failure.value.code is ErrorCode.CONFLICT
        current = await service.get("user:test")
        assert current.revision == 1
        assert current.customization == {
            "version": 1,
            "appearance": {"accent": "#112233"},
        }

    asyncio.run(scenario())


def test_frontend_preference_control_plane_binds_resource_to_authenticated_principal() -> None:
    async def scenario() -> None:
        control_plane, http = _control_plane()
        assert FRONTEND_PREFERENCE_COLLECTION in control_plane.registered_collections
        assert set(FRONTEND_PREFERENCE_COMMANDS).issubset(control_plane.registered_commands)

        manifest = await http.handle(
            HTTPRequest(method="GET", path="/api/v1", headers=_headers())
        )
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert FRONTEND_PREFERENCE_COLLECTION in manifest.body["resources"]
        assert set(FRONTEND_PREFERENCE_COMMANDS).issubset(manifest.body["commands"])

        initial = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{FRONTEND_PREFERENCE_COLLECTION}",
                headers=_headers(),
            )
        )
        assert initial.status == 200
        assert isinstance(initial.body, dict)
        item = initial.body["items"][0]
        assert item["id"] == "user:test"
        assert item["revision"] == 0
        assert item["customization"] is None

        saved = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/frontend-preference.update",
                headers=_headers(key="save-theme"),
                body={
                    "resource_ref": "user:test",
                    "schema_version": 1,
                    "expected_revision": 0,
                    "customization": {
                        "version": 1,
                        "appearance": {"accent": "#112233"},
                    },
                },
            )
        )
        assert saved.status == 200
        assert isinstance(saved.body, dict)
        assert saved.body["revision"] == 1

        cross_principal = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/frontend-preference.update",
                headers=_headers(key="cross-principal"),
                body={
                    "resource_ref": "user:other",
                    "schema_version": 1,
                    "expected_revision": 0,
                    "customization": {"version": 1},
                },
            )
        )
        assert cross_principal.status == 404
        assert isinstance(cross_principal.body, dict)
        assert cross_principal.body["code"] == "not_found"

    asyncio.run(scenario())
