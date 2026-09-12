from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ai_multi_agent_platform.cli.client import (
    APIClientError,
    ClientOptions,
    ControlPlaneClient,
    RawResponse,
)
from ai_multi_agent_platform.cli.profiles import CLIProfile, ProfileError, ProfileStore


class SequenceTransport:
    def __init__(self, responses: list[RawResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del method, url, headers, body, timeout
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def test_profile_store_is_versioned_non_secret_and_rejects_url_credentials(tmp_path: Path) -> None:
    path = tmp_path / "cli.json"
    store = ProfileStore.load(path)
    store.set_profile(
        "remote",
        CLIProfile(
            endpoint="https://control.example.test/base",
            principal_ref="user:test",
            owner_type="user",
            owner_id="test",
        ),
    )
    store.use("remote")
    store.save()

    loaded = ProfileStore.load(path)
    name, profile = loaded.resolve()
    assert name == "remote"
    assert profile.endpoint == "https://control.example.test/base"
    assert "token" not in path.read_text(encoding="utf-8")

    with pytest.raises(ProfileError, match="must not contain credentials"):
        CLIProfile(endpoint="https://user:secret@control.example.test")

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "current_profile": "remote",
                "profiles": {
                    "remote": {
                        "endpoint": "https://control.example.test",
                        "token": "must-not-be-stored",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="unsupported profile fields"):
        ProfileStore.load(path)


def test_get_retries_transient_status_but_post_is_not_automatically_replayed() -> None:
    unavailable = RawResponse(
        status=503,
        body=json.dumps(
            {
                "code": "unavailable",
                "category": "availability",
                "message": "temporarily unavailable",
                "retryable": True,
            }
        ).encode(),
        headers={},
    )
    healthy = RawResponse(
        status=200,
        body=b'{"ready":true}',
        headers={"x-api-version": "v1"},
    )
    transport = SequenceTransport([unavailable, healthy])
    client = ControlPlaneClient(
        ClientOptions(endpoint="http://control.test", retries=1),
        transport=transport,
    )
    response = client.get("/health")
    assert response.status == 200
    assert transport.calls == 2

    post_transport = SequenceTransport([unavailable, healthy])
    post_client = ControlPlaneClient(
        ClientOptions(endpoint="http://control.test", retries=5),
        transport=post_transport,
    )
    with pytest.raises(APIClientError) as exc_info:
        post_client.post("/tasks/task_1:cancel")
    assert exc_info.value.code == "unavailable"
    assert post_transport.calls == 1
