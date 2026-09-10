"""Concrete GitHub Releases connector over the canonical Connector boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ai_multi_agent_platform.configuration.secrets import SecretAccessContext, SecretProvider
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.security import redact_sensitive

from .models import (
    Connection,
    ConnectionStatus,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorDefinition,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalResourceReference,
    connector_definition_id,
)
from .provider import ConnectorProvider

GITHUB_RELEASE_CONNECTOR_TYPE = "github.releases"
GITHUB_RELEASE_CONNECTOR_VERSION = "1.0"
GITHUB_RELEASE_CREATE_ACTION = "github.release.create"
GITHUB_RELEASE_ASSET_ATTACH_ACTION = "github.release.asset.attach"
GITHUB_API_VERSION = "2026-03-10"
_DEFAULT_API_BASE_URL = "https://api.github.com"
_MANIFEST_ASSET_NAME = "application-release-manifest.json"
_CHECKSUMS_ASSET_NAME = "SHA256SUMS"


@dataclass(frozen=True, slots=True)
class GitHubRestResponse:
    status: int
    body: JsonValue | None = None
    headers: dict[str, str] = field(default_factory=dict)


class GitHubRestTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, JsonValue] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> GitHubRestResponse: ...


class UrllibGitHubRestTransport:
    """Dependency-free async facade over urllib for GitHub REST requests."""

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
        if json_body is not None and data is not None:
            raise ValueError("GitHub request cannot contain JSON and binary data together")
        return await asyncio.to_thread(
            self._request,
            method,
            url,
            headers,
            json_body,
            data,
            content_type,
        )

    @staticmethod
    def _request(
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, JsonValue] | None,
        data: bytes | None,
        content_type: str | None,
    ) -> GitHubRestResponse:
        body = data
        request_headers = dict(headers)
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif content_type is not None:
            request_headers["Content-Type"] = content_type
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS API boundary
                response_body = response.read()
                return GitHubRestResponse(
                    status=response.status,
                    body=_decode_response(response_body),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            return GitHubRestResponse(
                status=exc.code,
                body=_decode_response(exc.read()),
                headers={key.lower(): value for key, value in exc.headers.items()},
            )
        except URLError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "GitHub REST endpoint is unavailable",
                retryable=True,
            ) from exc


class GitHubReleaseConnectorProvider(ConnectorProvider):
    """Publish release metadata and canonical File assets through GitHub Releases."""

    def __init__(
        self,
        secret_provider: SecretProvider,
        files: FileProvider,
        *,
        transport: GitHubRestTransport | None = None,
        provider_id: str = "connector.github-releases",
    ) -> None:
        if not provider_id.strip():
            raise ValueError("GitHub release connector provider_id must not be blank")
        self._secret_provider = secret_provider
        self._files = files
        self._transport = transport or UrllibGitHubRestTransport()
        self._provider_id = provider_id
        self._connections: dict[str, Connection] = {}

    @property
    def definition(self) -> ConnectorDefinition:
        return ConnectorDefinition(
            id=connector_definition_id(
                GITHUB_RELEASE_CONNECTOR_TYPE,
                GITHUB_RELEASE_CONNECTOR_VERSION,
            ),
            connector_type_id=GITHUB_RELEASE_CONNECTOR_TYPE,
            name="GitHub Releases",
            version=GITHUB_RELEASE_CONNECTOR_VERSION,
            description=(
                "Publish application release metadata, manifests, checksums and canonical "
                "File assets through GitHub Releases."
            ),
            supported_operations=("validate", "health", "action.invoke"),
            features=("release-publishing", "binary-assets", "checksums"),
            authentication_requirements=("token",),
            actions=(
                GITHUB_RELEASE_CREATE_ACTION,
                GITHUB_RELEASE_ASSET_ATTACH_ACTION,
            ),
            configuration_schema={
                "type": "object",
                "properties": {
                    "api_base_url": {"type": "string"},
                    "api_version": {"type": "string"},
                },
                "additionalProperties": False,
            },
            health_semantics={
                "healthy": "GitHub accepted the configured credential",
                "unavailable": "GitHub could not be reached",
            },
            adapter_metadata=(
                AdapterMetadata(
                    namespace="github",
                    values={"rest_api": True, "api_version": GITHUB_API_VERSION},
                ),
            ),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="connector",
            supported_operations=self.definition.supported_operations,
            capabilities=tuple(
                Capability(
                    name=action,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for action in self.definition.actions
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def validate_connection(
        self,
        connection: Connection,
        context: OperationContext,
    ) -> Connection:
        self._require_connector_version(connection)
        if len(connection.secret_references) != 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "GitHub Releases connection requires exactly one token secret reference",
                provider_id=self._provider_id,
            )
        endpoint_metadata = dict(connection.endpoint_metadata)
        if redact_sensitive(endpoint_metadata) != endpoint_metadata:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "GitHub endpoint metadata must not contain credentials",
                provider_id=self._provider_id,
            )
        token = await self._token(connection, action="connector.validate")
        response = await self._transport.request(
            "GET",
            f"{self._api_base_url(connection)}/user",
            headers=self._headers(connection, token),
        )
        account = self._expect_object(response, expected={200}, operation="validate credential")
        login = account.get("login")
        account_id = account.get("id")
        now = datetime.now(UTC)
        normalized = replace(
            connection,
            status=ConnectionStatus.READY,
            health=HealthStatus.HEALTHY,
            granted_scopes=connection.requested_scopes,
            last_checked_at=now,
            updated_at=now,
            adapter_metadata=tuple(
                metadata
                for metadata in connection.adapter_metadata
                if metadata.namespace != GITHUB_RELEASE_CONNECTOR_TYPE
            )
            + (
                AdapterMetadata(
                    namespace=GITHUB_RELEASE_CONNECTOR_TYPE,
                    values={
                        "login": login if isinstance(login, str) else None,
                        "account_id": account_id if isinstance(account_id, int | str) else None,
                    },
                ),
            ),
        )
        self._connections[connection.id] = normalized
        return normalized

    async def connection_health(
        self,
        connection: Connection,
        context: OperationContext,
    ) -> HealthStatus:
        del context
        self._require_connector_version(connection)
        try:
            token = await self._token(connection, action="connector.health")
            response = await self._transport.request(
                "GET",
                f"{self._api_base_url(connection)}/user",
                headers=self._headers(connection, token),
            )
        except ContractError as exc:
            if exc.code is ErrorCode.UNAVAILABLE:
                return HealthStatus.UNAVAILABLE
            raise
        if response.status == 200:
            self._connections[connection.id] = connection
            return HealthStatus.HEALTHY
        if response.status in {401, 403}:
            return HealthStatus.DEGRADED
        return HealthStatus.UNAVAILABLE

    async def list_resources(
        self,
        query: ConnectorResourceQuery,
    ) -> tuple[ExternalResourceReference, ...]:
        del query
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "GitHub Releases connector does not expose canonical read resources",
            provider_id=self._provider_id,
        )

    async def read_resource(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> ExternalResourceReference:
        del connection, resource, context
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "GitHub Releases connector does not expose canonical read resources",
            provider_id=self._provider_id,
        )

    async def invoke_action(
        self,
        invocation: ConnectorActionInvocation,
    ) -> ConnectorActionResult:
        connection = self._connection(invocation.connection_id)
        token = await self._token(connection, action=invocation.action)
        if invocation.action == GITHUB_RELEASE_CREATE_ACTION:
            output = await self._create_release(connection, invocation, token)
        elif invocation.action == GITHUB_RELEASE_ASSET_ATTACH_ACTION:
            output = await self._attach_canonical_asset(connection, invocation, token)
        else:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"GitHub Releases connector does not expose action {invocation.action!r}",
                provider_id=self._provider_id,
            )
        return ConnectorActionResult(invocation_id=invocation.invocation_id, output=output)

    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        del request
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "GitHub Releases connector does not implement synchronization",
            provider_id=self._provider_id,
        )

    async def _create_release(
        self,
        connection: Connection,
        invocation: ConnectorActionInvocation,
        token: str,
    ) -> dict[str, JsonValue]:
        arguments = dict(invocation.arguments)
        repository = _repository_ref(arguments.get("repository_ref"))
        tag = _required_string(arguments, "tag")
        source_revision = _required_string(arguments, "source_revision")
        requested_visibility = _release_visibility(arguments.get("visibility"))
        headers = self._headers(connection, token)
        repository_path = _repository_path(repository)
        api_base = self._api_base_url(connection)

        repository_response = await self._transport.request(
            "GET",
            f"{api_base}/repos/{repository_path}",
            headers=headers,
        )
        repository_data = self._expect_object(
            repository_response,
            expected={200},
            operation="read repository",
        )
        private = repository_data.get("private")
        if not isinstance(private, bool):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "GitHub repository response has no boolean private field",
                provider_id=self._provider_id,
            )
        _validate_visibility(private=private, requested=requested_visibility)

        commit_response = await self._transport.request(
            "GET",
            f"{api_base}/repos/{repository_path}/commits/{quote(source_revision, safe='')}",
            headers=headers,
        )
        commit = self._expect_object(
            commit_response,
            expected={200},
            operation="resolve source revision",
        )
        canonical_source = _required_string(commit, "sha")

        tag_commit = await self._resolve_tag_commit(
            connection,
            repository,
            tag,
            token,
        )
        fail_if_tag_differs = arguments.get("fail_if_tag_points_elsewhere", True)
        if (
            fail_if_tag_differs is not False
            and tag_commit is not None
            and tag_commit != canonical_source
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "GitHub release tag already points to a different source revision",
                provider_id=self._provider_id,
                details={"tag": tag, "expected_source": canonical_source},
            )

        release_response = await self._transport.request(
            "GET",
            f"{api_base}/repos/{repository_path}/releases/tags/{quote(tag, safe='')}",
            headers=headers,
        )
        if release_response.status == 404:
            channel = _required_string(arguments, "channel")
            version = _required_string(arguments, "version")
            application_id = arguments.get("application_id")
            name = f"{application_id} {version}" if isinstance(application_id, str) else tag
            release_notes = arguments.get("release_notes")
            if release_notes is not None and not isinstance(release_notes, str):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "GitHub release notes must be a string or null",
                    provider_id=self._provider_id,
                )
            release_response = await self._transport.request(
                "POST",
                f"{api_base}/repos/{repository_path}/releases",
                headers=headers,
                json_body={
                    "tag_name": tag,
                    "target_commitish": canonical_source,
                    "name": name,
                    "body": release_notes or "",
                    "draft": False,
                    "prerelease": channel != "stable",
                    "generate_release_notes": False,
                    "make_latest": "true" if channel == "stable" else "false",
                },
            )
            release = self._expect_object(
                release_response,
                expected={201},
                operation="create release",
            )
        else:
            release = self._expect_object(
                release_response,
                expected={200},
                operation="resolve release",
            )
            if arguments.get("fail_if_release_exists_with_different_source", True) is not False:
                resolved = await self._resolve_tag_commit(connection, repository, tag, token)
                if resolved != canonical_source:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "GitHub release exists for a different source revision",
                        provider_id=self._provider_id,
                    )

        release_id = _external_id(release.get("id"), "GitHub release id")
        upload_url = _required_string(release, "upload_url")
        manifest = _json_object(arguments.get("manifest"), "manifest")
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        checksum_bytes = _checksum_document(manifest, manifest_digest)
        await self._upload_idempotent_asset(
            connection,
            repository,
            release_id,
            upload_url,
            _MANIFEST_ASSET_NAME,
            "application/json",
            manifest_bytes,
            manifest_digest,
            token,
        )
        await self._upload_idempotent_asset(
            connection,
            repository,
            release_id,
            upload_url,
            _CHECKSUMS_ASSET_NAME,
            "text/plain",
            checksum_bytes,
            hashlib.sha256(checksum_bytes).hexdigest(),
            token,
        )

        html_url = _required_string(release, "html_url")
        return {
            "release_url": html_url,
            "external_release_id": release_id,
            "visibility": requested_visibility,
            "latest_url": None,
            "source_revision": canonical_source,
            "manifest_sha256": manifest_digest,
        }

    async def _attach_canonical_asset(
        self,
        connection: Connection,
        invocation: ConnectorActionInvocation,
        token: str,
    ) -> dict[str, JsonValue]:
        arguments = dict(invocation.arguments)
        repository = _repository_ref(arguments.get("repository_ref"))
        release_id = _required_string(arguments, "external_release_id")
        file_id = _required_string(arguments, "file_id")
        filename = _asset_name(_required_string(arguments, "filename"))
        expected_sha256 = _sha256(_required_string(arguments, "sha256"))
        media_type = _required_string(arguments, "media_type")
        context = _data_context(invocation.context)
        record = await self._files.get_file(file_id, context)
        if record.sha256 != expected_sha256:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical File digest differs from the release artifact digest",
                provider_id=self._provider_id,
            )
        if not await self._files.verify_checksum(file_id, context):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical File failed checksum verification before GitHub upload",
                provider_id=self._provider_id,
            )
        data = bytearray()
        async for chunk in self._files.stream_file(file_id, context):
            data.extend(chunk)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "streamed canonical File digest changed before GitHub upload",
                provider_id=self._provider_id,
            )

        release_response = await self._transport.request(
            "GET",
            (
                f"{self._api_base_url(connection)}/repos/{_repository_path(repository)}"
                f"/releases/{quote(release_id, safe='')}"
            ),
            headers=self._headers(connection, token),
        )
        release = self._expect_object(
            release_response,
            expected={200},
            operation="resolve release for asset upload",
        )
        upload_url = _required_string(release, "upload_url")
        asset = await self._upload_idempotent_asset(
            connection,
            repository,
            release_id,
            upload_url,
            filename,
            media_type,
            bytes(data),
            expected_sha256,
            token,
        )
        return {
            "download_url": _required_string(asset, "browser_download_url"),
            "external_asset_id": _external_id(asset.get("id"), "GitHub asset id"),
            "sha256": expected_sha256,
        }

    async def _upload_idempotent_asset(
        self,
        connection: Connection,
        repository: str,
        release_id: str,
        upload_url: str,
        filename: str,
        media_type: str,
        data: bytes,
        expected_sha256: str,
        token: str,
    ) -> dict[str, JsonValue]:
        existing = await self._find_asset(connection, repository, release_id, filename, token)
        if existing is not None:
            _require_matching_asset_digest(existing, expected_sha256)
            return existing
        base_upload_url = upload_url.split("{", 1)[0]
        separator = "&" if "?" in base_upload_url else "?"
        response = await self._transport.request(
            "POST",
            f"{base_upload_url}{separator}{urlencode({'name': filename})}",
            headers=self._headers(connection, token),
            data=data,
            content_type=media_type,
        )
        if response.status == 422:
            raced = await self._find_asset(connection, repository, release_id, filename, token)
            if raced is not None:
                _require_matching_asset_digest(raced, expected_sha256)
                return raced
        asset = self._expect_object(
            response,
            expected={201},
            operation=f"upload release asset {filename}",
        )
        _require_matching_asset_digest(asset, expected_sha256)
        return asset

    async def _find_asset(
        self,
        connection: Connection,
        repository: str,
        release_id: str,
        filename: str,
        token: str,
    ) -> dict[str, JsonValue] | None:
        response = await self._transport.request(
            "GET",
            (
                f"{self._api_base_url(connection)}/repos/{_repository_path(repository)}"
                f"/releases/{quote(release_id, safe='')}/assets?per_page=100"
            ),
            headers=self._headers(connection, token),
        )
        items = self._expect_array(response, expected={200}, operation="list release assets")
        matches = [
            item for item in items if isinstance(item, dict) and item.get("name") == filename
        ]
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "GitHub release contains duplicate asset names",
                provider_id=self._provider_id,
            )
        return cast(dict[str, JsonValue], matches[0]) if matches else None

    async def _resolve_tag_commit(
        self,
        connection: Connection,
        repository: str,
        tag: str,
        token: str,
    ) -> str | None:
        api_base = self._api_base_url(connection)
        repository_path = _repository_path(repository)
        response = await self._transport.request(
            "GET",
            f"{api_base}/repos/{repository_path}/git/ref/tags/{quote(tag, safe='')}",
            headers=self._headers(connection, token),
        )
        if response.status == 404:
            return None
        ref = self._expect_object(response, expected={200}, operation="resolve release tag")
        obj = _json_object(ref.get("object"), "tag object")
        object_type = _required_string(obj, "type")
        sha = _required_string(obj, "sha")
        for _ in range(8):
            if object_type == "commit":
                return sha
            if object_type != "tag":
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "GitHub tag resolves to an unsupported object type",
                    provider_id=self._provider_id,
                )
            tag_response = await self._transport.request(
                "GET",
                f"{api_base}/repos/{repository_path}/git/tags/{quote(sha, safe='')}",
                headers=self._headers(connection, token),
            )
            annotated = self._expect_object(
                tag_response,
                expected={200},
                operation="resolve annotated release tag",
            )
            obj = _json_object(annotated.get("object"), "annotated tag object")
            object_type = _required_string(obj, "type")
            sha = _required_string(obj, "sha")
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "GitHub annotated tag chain is unexpectedly deep",
            provider_id=self._provider_id,
        )

    async def _token(self, connection: Connection, *, action: str) -> str:
        if not connection.secret_references:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "GitHub Releases connection has no token secret reference",
                provider_id=self._provider_id,
            )
        material = await self._secret_provider.resolve(
            connection.secret_references[0],
            SecretAccessContext(
                consumer_ref=self._provider_id,
                project_id=connection.project_id,
                action=action,
                purpose="github-api-token",
            ),
        )
        return material.reveal()

    def _connection(self, connection_id: str) -> Connection:
        try:
            return self._connections[connection_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "GitHub Releases connection has not been validated by this provider process",
                provider_id=self._provider_id,
            ) from exc

    def _require_connector_version(self, connection: Connection) -> None:
        if (
            connection.connector_type_id != GITHUB_RELEASE_CONNECTOR_TYPE
            or connection.connector_version != GITHUB_RELEASE_CONNECTOR_VERSION
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "connection does not target the supported GitHub Releases connector version",
                provider_id=self._provider_id,
            )

    def _api_base_url(self, connection: Connection) -> str:
        value = connection.endpoint_metadata.get("api_base_url", _DEFAULT_API_BASE_URL)
        if not isinstance(value, str) or not value.startswith("https://"):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "GitHub api_base_url must be an HTTPS URL",
                provider_id=self._provider_id,
            )
        return value.rstrip("/")

    def _headers(self, connection: Connection, token: str) -> dict[str, str]:
        version = connection.endpoint_metadata.get("api_version", GITHUB_API_VERSION)
        if not isinstance(version, str) or not version.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "GitHub api_version must be a non-blank string",
                provider_id=self._provider_id,
            )
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": version,
            "User-Agent": "ai-multi-agent-platform",
        }

    def _expect_object(
        self,
        response: GitHubRestResponse,
        *,
        expected: set[int],
        operation: str,
    ) -> dict[str, JsonValue]:
        self._expect_status(response, expected=expected, operation=operation)
        if not isinstance(response.body, dict):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"GitHub {operation} response is not an object",
                provider_id=self._provider_id,
            )
        return cast(dict[str, JsonValue], response.body)

    def _expect_array(
        self,
        response: GitHubRestResponse,
        *,
        expected: set[int],
        operation: str,
    ) -> list[JsonValue]:
        self._expect_status(response, expected=expected, operation=operation)
        if not isinstance(response.body, list):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"GitHub {operation} response is not an array",
                provider_id=self._provider_id,
            )
        return cast(list[JsonValue], response.body)

    def _expect_status(
        self,
        response: GitHubRestResponse,
        *,
        expected: set[int],
        operation: str,
    ) -> None:
        if response.status in expected:
            return
        code = (
            ErrorCode.UNAUTHORIZED
            if response.status == 401
            else ErrorCode.FORBIDDEN
            if response.status == 403
            else ErrorCode.NOT_FOUND
            if response.status == 404
            else ErrorCode.CONFLICT
            if response.status in {409, 422}
            else ErrorCode.RATE_LIMITED
            if response.status == 429
            else ErrorCode.BACKEND_ERROR
        )
        raise ContractError(
            code,
            f"GitHub {operation} failed with HTTP {response.status}",
            retryable=response.status >= 500 or response.status == 429,
            provider_id=self._provider_id,
        )


def _decode_response(data: bytes) -> JsonValue | None:
    if not data:
        return None
    try:
        return cast(JsonValue, json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _repository_ref(value: JsonValue | None) -> str:
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "repository_ref must be owner/repository")
    pieces = value.split("/")
    if (
        len(pieces) != 2
        or any(not piece.strip() or piece in {".", ".."} for piece in pieces)
        or any(char.isspace() for char in value)
    ):
        raise ContractError(ErrorCode.INVALID_REQUEST, "repository_ref must be owner/repository")
    return value


def _repository_path(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"{quote(owner, safe='')}/{quote(name, safe='')}"


def _required_string(value: dict[str, JsonValue], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a non-blank string")
    return item


def _json_object(value: JsonValue | None, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be an object")
    return cast(dict[str, JsonValue], value)


def _external_id(value: JsonValue | None, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).strip():
        raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, f"{label} is missing or invalid")
    return str(value)


def _sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ContractError(ErrorCode.INVALID_REQUEST, "sha256 must be a 64-character hex digest")
    return normalized


def _asset_name(value: str) -> str:
    if value in {".", ".."} or "/" in value or "\\" in value or "\n" in value or "\r" in value:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "GitHub release asset filename must be a single safe path segment",
        )
    return value


def _release_visibility(value: JsonValue | None) -> str:
    if value not in {"public", "authenticated", "private"}:
        raise ContractError(ErrorCode.INVALID_REQUEST, "unsupported release visibility")
    return cast(str, value)


def _validate_visibility(*, private: bool, requested: str) -> None:
    if private and requested == "public":
        raise ContractError(
            ErrorCode.CONFLICT,
            "a private GitHub repository cannot provide a public release download",
        )
    if not private and requested != "public":
        raise ContractError(
            ErrorCode.CONFLICT,
            "GitHub releases in a public repository cannot be made private per release",
        )


def _require_matching_asset_digest(asset: dict[str, JsonValue], expected_sha256: str) -> None:
    digest = asset.get("digest")
    expected = f"sha256:{expected_sha256}"
    if digest != expected:
        raise ContractError(
            ErrorCode.CONFLICT,
            "GitHub release asset exists but its digest does not match the canonical artifact",
            details={"asset_name": asset.get("name")},
        )


def _checksum_document(manifest: dict[str, JsonValue], manifest_digest: str) -> bytes:
    lines = [f"{manifest_digest}  {_MANIFEST_ASSET_NAME}"]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "release manifest artifacts must be an array",
        )
    entries: list[tuple[str, str]] = []
    for artifact in artifacts:
        data = _json_object(artifact, "release manifest artifact")
        filename = _asset_name(_required_string(data, "filename"))
        digest = _sha256(_required_string(data, "sha256"))
        entries.append((filename, digest))
    for filename, digest in sorted(entries):
        lines.append(f"{digest}  {filename}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _data_context(operation: OperationContext) -> DataAccessContext:
    actor_ref = (
        f"{operation.owner_type}:{operation.owner_id}"
        if operation.owner_type is not None and operation.owner_id is not None
        else "service:platform"
    )
    return DataAccessContext(
        operation=operation,
        actor_ref=actor_ref,
        audit_metadata={"source": "github-release-connector"},
    )


__all__ = [
    "GITHUB_API_VERSION",
    "GITHUB_RELEASE_ASSET_ATTACH_ACTION",
    "GITHUB_RELEASE_CONNECTOR_TYPE",
    "GITHUB_RELEASE_CONNECTOR_VERSION",
    "GITHUB_RELEASE_CREATE_ACTION",
    "GitHubReleaseConnectorProvider",
    "GitHubRestResponse",
    "GitHubRestTransport",
    "UrllibGitHubRestTransport",
]
