from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    GITHUB_RELEASE_ASSET_ATTACH_ACTION,
    GITHUB_RELEASE_CONNECTOR_TYPE,
    GITHUB_RELEASE_CONNECTOR_VERSION,
    GITHUB_RELEASE_CREATE_ACTION,
    Connection,
    ConnectorActionInvocation,
    GitHubReleaseConnectorProvider,
    GitHubRestResponse,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference

_REPOSITORY = "ScoreSymphony/example-app"
_API = "https://api.github.com/repos/ScoreSymphony/example-app"
_UPLOAD = "https://uploads.github.com/repos/ScoreSymphony/example-app/releases/123/assets"
_TOKEN = "issue751-fixture-credential-DO-NOT-LEAK"
_SOURCE = "a" * 40
_OTHER_SOURCE = "b" * 40


class _ScenarioTransport:
    def __init__(
        self,
        *,
        source_revision: str = _SOURCE,
        private: bool = False,
        tag_sha: str | None = None,
        release_exists: bool = False,
        tag_resolutions: tuple[str | None, ...] = (),
        fail_on: str | None = None,
        uncertain_create_once: bool = False,
    ) -> None:
        self.source_revision = source_revision
        self.private = private
        self.tag_sha = tag_sha
        self.release_exists = release_exists
        self.tag_resolutions = list(tag_resolutions)
        self.fail_on = fail_on
        self.uncertain_create_once = uncertain_create_once
        self.assets: list[dict[str, JsonValue]] = []
        self.requests: list[tuple[str, str]] = []
        self._tag_reads = 0
        self._uncertain_create_raised = False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, JsonValue] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> GitHubRestResponse:
        del content_type
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"
        self.requests.append((method, url))

        if method == "GET" and url == "https://api.github.com/user":
            return GitHubRestResponse(status=200, body={"login": "tester", "id": 751})
        if method == "GET" and url == _API:
            if self.fail_on == "repository":
                return GitHubRestResponse(status=503, body={"message": _TOKEN})
            return GitHubRestResponse(status=200, body={"private": self.private})
        if method == "GET" and url == f"{_API}/commits/{self.source_revision}":
            if self.fail_on == "source":
                return GitHubRestResponse(status=503, body={"message": _TOKEN})
            return GitHubRestResponse(status=200, body={"sha": self.source_revision})
        if method == "GET" and url == f"{_API}/git/ref/tags/v1.0.0":
            value = self._next_tag_resolution()
            if value is None:
                return GitHubRestResponse(status=404, body={"message": "Not Found"})
            return GitHubRestResponse(
                status=200,
                body={"object": {"type": "commit", "sha": value}},
            )
        if method == "GET" and url == f"{_API}/releases/tags/v1.0.0":
            if self.fail_on == "release_lookup":
                return GitHubRestResponse(status=503, body={"message": _TOKEN})
            if not self.release_exists:
                return GitHubRestResponse(status=404, body={"message": "Not Found"})
            return GitHubRestResponse(status=200, body=self._release())
        if method == "POST" and url == f"{_API}/releases":
            assert json_body is not None
            assert json_body["target_commitish"] == self.source_revision
            if self.fail_on == "release_create":
                return GitHubRestResponse(status=503, body={"message": _TOKEN})
            self.release_exists = True
            self.tag_sha = self.source_revision
            if self.uncertain_create_once and not self._uncertain_create_raised:
                self._uncertain_create_raised = True
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "GitHub REST endpoint is unavailable after release creation",
                    retryable=True,
                )
            return GitHubRestResponse(status=201, body=self._release())
        if method == "GET" and url == f"{_API}/releases/123":
            return GitHubRestResponse(status=200, body=self._release())
        if method == "GET" and url == f"{_API}/releases/123/assets?per_page=100":
            return GitHubRestResponse(status=200, body=list(self.assets))
        if method == "POST" and url.startswith(f"{_UPLOAD}?"):
            assert data is not None
            name = parse_qs(urlparse(url).query)["name"][0]
            failure = {
                "application-release-manifest.json": "manifest_upload",
                "SHA256SUMS": "checksums_upload",
                "example-app.bin": "application_upload",
            }.get(name)
            if failure is not None and self.fail_on == failure:
                return GitHubRestResponse(status=503, body={"message": _TOKEN})
            digest = hashlib.sha256(data).hexdigest()
            asset: dict[str, JsonValue] = {
                "id": len(self.assets) + 1000,
                "name": name,
                "digest": f"sha256:{digest}",
                "browser_download_url": (
                    "https://github.com/ScoreSymphony/example-app/releases/download/"
                    f"v1.0.0/{name}"
                ),
            }
            self.assets.append(asset)
            return GitHubRestResponse(status=201, body=asset)
        raise AssertionError(f"unexpected GitHub request: {method} {url}")

    def _next_tag_resolution(self) -> str | None:
        if self._tag_reads < len(self.tag_resolutions):
            value = self.tag_resolutions[self._tag_reads]
            self._tag_reads += 1
            return value
        self._tag_reads += 1
        return self.tag_sha

    @staticmethod
    def _release() -> dict[str, JsonValue]:
        return {
            "id": 123,
            "upload_url": (
                "https://uploads.github.com/repos/ScoreSymphony/example-app/"
                "releases/123/assets{?name,label}"
            ),
            "html_url": "https://github.com/ScoreSymphony/example-app/releases/tag/v1.0.0",
        }


@dataclass(slots=True)
class _Fixture:
    provider: GitHubReleaseConnectorProvider
    connection: Connection
    operation: OperationContext
    transport: _ScenarioTransport
    file_id: str
    sha256: str


async def _fixture(tmp_path: Path, transport: _ScenarioTransport) -> _Fixture:
    project_id = new_id("project")
    operation = OperationContext(
        correlation_id="issue751-github-conformance",
        owner_type="user",
        owner_id="tester",
        project_id=project_id,
    )
    data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    record = await files.create_file(
        b"issue-751-packaged-application",
        data_context,
        content_type="application/octet-stream",
    )
    secrets = LocalSecretProvider()
    secret = SecretReference(
        provider="local-secrets",
        secret_id="issue751-github-token",
        scope=project_id,
    )
    await secrets.create(
        secret,
        _TOKEN,
        purpose="github-api-token",
        allowed_consumers=("connector.github-releases",),
        allowed_purposes=("github-api-token",),
    )
    provider = GitHubReleaseConnectorProvider(secrets, files, transport=transport)
    connection = Connection(
        id=new_id("connection"),
        connector_type_id=GITHUB_RELEASE_CONNECTOR_TYPE,
        connector_version=GITHUB_RELEASE_CONNECTOR_VERSION,
        owner_type="user",
        owner_id="tester",
        display_name="Issue 751 GitHub fixture",
        project_id=project_id,
        secret_references=(secret,),
    )
    await provider.validate_connection(connection, operation)
    return _Fixture(
        provider=provider,
        connection=connection,
        operation=operation,
        transport=transport,
        file_id=record.file_id,
        sha256=record.sha256,
    )


def _manifest(sha256: str) -> dict[str, JsonValue]:
    return {
        "schema_version": "1",
        "application": "example-app",
        "version": "1.0.0",
        "channel": "stable",
        "source_revision": _SOURCE,
        "artifacts": [
            {
                "target": "linux-x64",
                "filename": "example-app.bin",
                "sha256": sha256,
            }
        ],
    }


def _create_invocation(
    fixture: _Fixture,
    *,
    visibility: str = "public",
) -> ConnectorActionInvocation:
    return ConnectorActionInvocation(
        invocation_id="issue751-create-release",
        connection_id=fixture.connection.id,
        action=GITHUB_RELEASE_CREATE_ACTION,
        arguments={
            "repository_ref": _REPOSITORY,
            "application_id": "example-app",
            "tag": "v1.0.0",
            "version": "1.0.0",
            "channel": "stable",
            "visibility": visibility,
            "source_revision": _SOURCE,
            "release_notes": "Issue 751 fixture",
            "manifest": _manifest(fixture.sha256),
        },
        context=fixture.operation,
    )


def _asset_invocation(fixture: _Fixture) -> ConnectorActionInvocation:
    return ConnectorActionInvocation(
        invocation_id="issue751-attach-asset",
        connection_id=fixture.connection.id,
        action=GITHUB_RELEASE_ASSET_ATTACH_ACTION,
        arguments={
            "repository_ref": _REPOSITORY,
            "external_release_id": "123",
            "artifact_id": new_id("artifact"),
            "file_id": fixture.file_id,
            "filename": "example-app.bin",
            "sha256": fixture.sha256,
            "media_type": "application/octet-stream",
        },
        context=fixture.operation,
    )


def _post_count(transport: _ScenarioTransport, fragment: str) -> int:
    return sum(method == "POST" and fragment in url for method, url in transport.requests)


def test_same_provenance_release_and_same_digest_assets_are_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport()
        fixture = await _fixture(tmp_path, transport)

        first = await fixture.provider.invoke_action(_create_invocation(fixture))
        second = await fixture.provider.invoke_action(_create_invocation(fixture))
        assert first.output == second.output
        assert _post_count(transport, f"{_API}/releases") == 1
        assert _post_count(transport, _UPLOAD) == 2

        first_asset = await fixture.provider.invoke_action(_asset_invocation(fixture))
        second_asset = await fixture.provider.invoke_action(_asset_invocation(fixture))
        assert first_asset.output == second_asset.output
        assert _post_count(transport, _UPLOAD) == 3
        assert [asset["name"] for asset in transport.assets].count("example-app.bin") == 1

    asyncio.run(scenario())


def test_conflicting_existing_tag_fails_without_retagging(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport(tag_sha=_OTHER_SOURCE)
        fixture = await _fixture(tmp_path, transport)
        with pytest.raises(ContractError) as caught:
            await fixture.provider.invoke_action(_create_invocation(fixture))
        assert caught.value.code is ErrorCode.CONFLICT
        assert _post_count(transport, f"{_API}/releases") == 0
        assert transport.tag_sha == _OTHER_SOURCE

    asyncio.run(scenario())


def test_existing_release_with_conflicting_provenance_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport(
            release_exists=True,
            tag_sha=_OTHER_SOURCE,
            tag_resolutions=(_SOURCE, _OTHER_SOURCE),
        )
        fixture = await _fixture(tmp_path, transport)
        with pytest.raises(ContractError) as caught:
            await fixture.provider.invoke_action(_create_invocation(fixture))
        assert caught.value.code is ErrorCode.CONFLICT
        assert _post_count(transport, f"{_API}/releases") == 0
        assert transport.assets == []

    asyncio.run(scenario())


def test_same_name_different_digest_asset_conflicts_without_replacement(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport()
        fixture = await _fixture(tmp_path, transport)
        await fixture.provider.invoke_action(_create_invocation(fixture))
        await fixture.provider.invoke_action(_asset_invocation(fixture))
        application_asset = next(
            item for item in transport.assets if item["name"] == "example-app.bin"
        )
        application_asset["digest"] = f"sha256:{'0' * 64}"
        posts_before = _post_count(transport, _UPLOAD)

        with pytest.raises(ContractError) as caught:
            await fixture.provider.invoke_action(_asset_invocation(fixture))
        assert caught.value.code is ErrorCode.CONFLICT
        assert _post_count(transport, _UPLOAD) == posts_before
        assert [asset["name"] for asset in transport.assets].count("example-app.bin") == 1
        assert application_asset["digest"] == f"sha256:{'0' * 64}"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("fail_on", "expected_code"),
    (
        ("repository", ErrorCode.BACKEND_ERROR),
        ("source", ErrorCode.BACKEND_ERROR),
        ("release_lookup", ErrorCode.BACKEND_ERROR),
        ("release_create", ErrorCode.BACKEND_ERROR),
        ("manifest_upload", ErrorCode.BACKEND_ERROR),
        ("checksums_upload", ErrorCode.BACKEND_ERROR),
    ),
)
def test_provider_failures_preserve_canonical_error_category_and_redact_fixture_credential(
    tmp_path: Path,
    fail_on: str,
    expected_code: ErrorCode,
) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport(fail_on=fail_on)
        fixture = await _fixture(tmp_path, transport)
        with pytest.raises(ContractError) as caught:
            await fixture.provider.invoke_action(_create_invocation(fixture))
        assert caught.value.code is expected_code
        assert _TOKEN not in str(caught.value)
        assert _TOKEN not in json.dumps(caught.value.details, sort_keys=True)

    asyncio.run(scenario())


def test_application_asset_upload_failure_is_explicit_and_credential_safe(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport()
        fixture = await _fixture(tmp_path, transport)
        created = await fixture.provider.invoke_action(_create_invocation(fixture))
        assert _TOKEN not in json.dumps(created.output, sort_keys=True)
        transport.fail_on = "application_upload"

        with pytest.raises(ContractError) as caught:
            await fixture.provider.invoke_action(_asset_invocation(fixture))
        assert caught.value.code is ErrorCode.BACKEND_ERROR
        assert _TOKEN not in str(caught.value)
        assert not any(asset["name"] == "example-app.bin" for asset in transport.assets)

    asyncio.run(scenario())


def test_uncertain_release_create_retry_resolves_existing_release_without_duplicate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = _ScenarioTransport(uncertain_create_once=True)
        fixture = await _fixture(tmp_path, transport)
        with pytest.raises(ContractError) as first:
            await fixture.provider.invoke_action(_create_invocation(fixture))
        assert first.value.code is ErrorCode.UNAVAILABLE
        assert transport.release_exists is True

        retried = await fixture.provider.invoke_action(_create_invocation(fixture))
        assert isinstance(retried.output, dict)
        assert retried.output["external_release_id"] == "123"
        assert _post_count(transport, f"{_API}/releases") == 1
        assert {asset["name"] for asset in transport.assets} == {
            "application-release-manifest.json",
            "SHA256SUMS",
        }

    asyncio.run(scenario())


def test_private_and_public_access_semantics_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        private_transport = _ScenarioTransport(private=True)
        private_fixture = await _fixture(tmp_path / "private", private_transport)
        private_result = await private_fixture.provider.invoke_action(
            _create_invocation(private_fixture, visibility="private")
        )
        assert isinstance(private_result.output, dict)
        assert private_result.output["visibility"] == "private"
        assert _TOKEN not in json.dumps(private_result.output, sort_keys=True)

        private_public_transport = _ScenarioTransport(private=True)
        private_public_fixture = await _fixture(
            tmp_path / "private-public",
            private_public_transport,
        )
        with pytest.raises(ContractError) as private_public:
            await private_public_fixture.provider.invoke_action(
                _create_invocation(private_public_fixture, visibility="public")
            )
        assert private_public.value.code is ErrorCode.CONFLICT

        public_private_transport = _ScenarioTransport(private=False)
        public_private_fixture = await _fixture(
            tmp_path / "public-private",
            public_private_transport,
        )
        with pytest.raises(ContractError) as public_private:
            await public_private_fixture.provider.invoke_action(
                _create_invocation(public_private_fixture, visibility="authenticated")
            )
        assert public_private.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())
