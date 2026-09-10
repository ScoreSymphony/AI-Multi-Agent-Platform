from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorActionInvocation,
    GITHUB_RELEASE_ASSET_ATTACH_ACTION,
    GITHUB_RELEASE_CONNECTOR_TYPE,
    GITHUB_RELEASE_CONNECTOR_VERSION,
    GITHUB_RELEASE_CREATE_ACTION,
    GitHubReleaseConnectorProvider,
    GitHubRestResponse,
)
from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference


class _GitHubReleaseFixtureTransport:
    def __init__(self, source_revision: str) -> None:
        self.source_revision = source_revision
        self.assets: list[dict[str, JsonValue]] = []
        self.requests: list[tuple[str, str]] = []

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
        del headers, content_type
        self.requests.append((method, url))
        api = "https://api.github.com/repos/ScoreSymphony/example-app"
        upload = "https://uploads.github.com/repos/ScoreSymphony/example-app/releases/123/assets"
        if method == "GET" and url == "https://api.github.com/user":
            return GitHubRestResponse(status=200, body={"login": "tester", "id": 42})
        if method == "GET" and url == api:
            return GitHubRestResponse(status=200, body={"private": False})
        if method == "GET" and url == f"{api}/commits/{self.source_revision}":
            return GitHubRestResponse(status=200, body={"sha": self.source_revision})
        if method == "GET" and url == f"{api}/git/ref/tags/v1.0.0":
            return GitHubRestResponse(status=404, body={"message": "Not Found"})
        if method == "GET" and url == f"{api}/releases/tags/v1.0.0":
            return GitHubRestResponse(status=404, body={"message": "Not Found"})
        if method == "POST" and url == f"{api}/releases":
            assert json_body is not None
            assert json_body["tag_name"] == "v1.0.0"
            assert json_body["target_commitish"] == self.source_revision
            return GitHubRestResponse(status=201, body=self._release())
        if method == "GET" and url == f"{api}/releases/123":
            return GitHubRestResponse(status=200, body=self._release())
        if method == "GET" and url == f"{api}/releases/123/assets?per_page=100":
            return GitHubRestResponse(status=200, body=list(self.assets))
        if method == "POST" and url.startswith(f"{upload}?"):
            assert data is not None
            query = parse_qs(urlparse(url).query)
            name = query["name"][0]
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


def test_github_release_connector_publishes_manifest_checksums_and_canonical_asset(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        source_revision = "a" * 40
        operation = OperationContext(
            correlation_id="github-release-publish-fixture",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )
        data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        artifact_bytes = b"packaged-application"
        file_record = await files.create_file(
            artifact_bytes,
            data_context,
            content_type="application/octet-stream",
        )
        secrets = LocalSecretProvider()
        secret = SecretReference(
            provider="local-secrets",
            secret_id="github-release-token",
            scope=project_id,
        )
        await secrets.create(
            secret,
            "fixture-token",
            purpose="github-api-token",
            allowed_consumers=("connector.github-releases",),
            allowed_purposes=("github-api-token",),
        )
        transport = _GitHubReleaseFixtureTransport(source_revision)
        provider = GitHubReleaseConnectorProvider(
            secrets,
            files,
            transport=transport,
        )
        connection = Connection(
            id=new_id("connection"),
            connector_type_id=GITHUB_RELEASE_CONNECTOR_TYPE,
            connector_version=GITHUB_RELEASE_CONNECTOR_VERSION,
            owner_type="user",
            owner_id="tester",
            display_name="GitHub Releases fixture",
            project_id=project_id,
            secret_references=(secret,),
        )
        await provider.validate_connection(connection, operation)
        manifest: dict[str, JsonValue] = {
            "schema_version": "1",
            "application": "example-app",
            "version": "1.0.0",
            "channel": "stable",
            "source_revision": source_revision,
            "artifacts": [
                {
                    "target": "linux-x64",
                    "filename": "example-app.bin",
                    "sha256": file_record.sha256,
                }
            ],
        }
        created = await provider.invoke_action(
            ConnectorActionInvocation(
                invocation_id="github-release-create-fixture",
                connection_id=connection.id,
                action=GITHUB_RELEASE_CREATE_ACTION,
                arguments={
                    "repository_ref": "ScoreSymphony/example-app",
                    "application_id": "example-app",
                    "tag": "v1.0.0",
                    "version": "1.0.0",
                    "channel": "stable",
                    "visibility": "public",
                    "source_revision": source_revision,
                    "release_notes": "Fixture release",
                    "manifest": manifest,
                },
                context=operation,
            )
        )
        assert isinstance(created.output, dict)
        assert created.output["external_release_id"] == "123"
        assert created.output["visibility"] == "public"
        assert created.output["release_url"] == (
            "https://github.com/ScoreSymphony/example-app/releases/tag/v1.0.0"
        )
        assert {asset["name"] for asset in transport.assets} == {
            "application-release-manifest.json",
            "SHA256SUMS",
        }

        attached = await provider.invoke_action(
            ConnectorActionInvocation(
                invocation_id="github-release-asset-fixture",
                connection_id=connection.id,
                action=GITHUB_RELEASE_ASSET_ATTACH_ACTION,
                arguments={
                    "repository_ref": "ScoreSymphony/example-app",
                    "external_release_id": "123",
                    "artifact_id": new_id("artifact"),
                    "file_id": file_record.file_id,
                    "filename": "example-app.bin",
                    "sha256": file_record.sha256,
                    "media_type": "application/octet-stream",
                },
                context=operation,
            )
        )
        assert isinstance(attached.output, dict)
        assert attached.output["sha256"] == file_record.sha256
        assert attached.output["download_url"] == (
            "https://github.com/ScoreSymphony/example-app/releases/download/"
            "v1.0.0/example-app.bin"
        )
        assert {asset["name"] for asset in transport.assets} == {
            "application-release-manifest.json",
            "SHA256SUMS",
            "example-app.bin",
        }

    asyncio.run(scenario())
