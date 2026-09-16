"""Small self-hosted browser reference adapter using only the Python standard library.

The adapter intentionally does not implement JavaScript or screenshots. Those features remain
provider metadata so a Playwright/CDP/remote-browser implementation can replace this adapter
without changing canonical capability requests.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from ai_multi_agent_platform.capabilities.types import CapabilityRegistration
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import FileProvider
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.data.models import DataAccessContext, FileRecord
from ai_multi_agent_platform.domain import new_id, validate_id

from .contracts import BrowserProvider
from .models import (
    BrowserNetworkPolicy,
    BrowserOperation,
    BrowserPrivacyClassification,
    BrowserProviderFeatures,
    BrowserSessionRef,
)
from .policy import BrowserNetworkPolicyHook, DefaultBrowserNetworkPolicyHook
from .reference_capabilities import (
    BROWSER_CLOSE_SESSION_CAPABILITY_ID as BROWSER_CLOSE_SESSION_CAPABILITY_ID,
)
from .reference_capabilities import (
    BROWSER_DOWNLOAD_CAPABILITY_ID as BROWSER_DOWNLOAD_CAPABILITY_ID,
)
from .reference_capabilities import (
    BROWSER_EXTRACT_CAPABILITY_ID as BROWSER_EXTRACT_CAPABILITY_ID,
)
from .reference_capabilities import (
    BROWSER_FOLLOW_LINK_CAPABILITY_ID as BROWSER_FOLLOW_LINK_CAPABILITY_ID,
)
from .reference_capabilities import (
    BROWSER_NAVIGATE_CAPABILITY_ID as BROWSER_NAVIGATE_CAPABILITY_ID,
)
from .reference_capabilities import (
    BROWSER_SUBMIT_FORM_CAPABILITY_ID as BROWSER_SUBMIT_FORM_CAPABILITY_ID,
)
from .reference_capabilities import (
    CLOSE_SESSION_TOOL_REF,
    CONTENT_TRUST,
    DOWNLOAD_TOOL_REF,
    EXTRACT_TOOL_REF,
    FOLLOW_LINK_TOOL_REF,
    NAVIGATE_TOOL_REF,
    SUBMIT_FORM_TOOL_REF,
    browser_capability_registrations,
)
from .reference_http import FetchedResource, ReferenceBrowserTransport
from .reference_page import (
    SessionState,
    decode_page,
    page_summary,
    parse_page,
    store_page,
)

_PROVIDER_ID = "browser.stdlib.reference"


class DownloadValidationHook(Protocol):
    """Replaceable validation hook run before browser downloads enter FileProvider storage."""

    def validate(
        self,
        *,
        url: str,
        content_type: str | None,
        data: bytes,
        context: OperationContext,
    ) -> None: ...


@runtime_checkable
class ArtifactLinkingFileProvider(Protocol):
    """Refined #13 file seam needed to link a downloaded file to an artifact identity."""

    async def link_artifact(
        self,
        file_id: str,
        artifact_id: str,
        context: DataAccessContext,
    ) -> FileRecord: ...


class DefaultDownloadValidationHook:
    """Conservative baseline hook; deployments can replace it with malware scanning."""

    _blocked_content_types = frozenset(
        {
            "application/x-dosexec",
            "application/x-msdownload",
            "application/x-executable",
        }
    )

    def validate(
        self,
        *,
        url: str,
        content_type: str | None,
        data: bytes,
        context: OperationContext,
    ) -> None:
        del url, data, context
        normalized = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
        if normalized in self._blocked_content_types:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"browser download content type is blocked by validation policy: {normalized}",
            )


class StdlibBrowserProvider(BrowserProvider):
    """HTTP/HTML reference browser proving the replaceable capability boundary."""

    def __init__(
        self,
        file_provider: FileProvider,
        *,
        network_policy: BrowserNetworkPolicy | None = None,
        network_policy_hook: BrowserNetworkPolicyHook | None = None,
        download_validation_hook: DownloadValidationHook | None = None,
        request_timeout_seconds: float = 30.0,
        session_ttl_seconds: float = 30 * 60,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        if session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be greater than zero")
        self._files = file_provider
        self._network_policy = network_policy or BrowserNetworkPolicy()
        network_hook = network_policy_hook or DefaultBrowserNetworkPolicyHook(self._network_policy)
        self._download_validation = download_validation_hook or DefaultDownloadValidationHook()
        self._session_ttl_seconds = session_ttl_seconds
        self._sessions: dict[str, SessionState] = {}
        self._transport = ReferenceBrowserTransport(
            network_policy=self._network_policy,
            network_hook=network_hook,
            request_timeout_seconds=request_timeout_seconds,
            provider_id=_PROVIDER_ID,
        )
        self._features = BrowserProviderFeatures(
            operations=(
                BrowserOperation.NAVIGATE,
                BrowserOperation.EXTRACT,
                BrowserOperation.FOLLOW_LINK,
                BrowserOperation.SUBMIT_FORM,
                BrowserOperation.DOWNLOAD,
                BrowserOperation.CLOSE_SESSION,
            ),
            headless=True,
            interactive=False,
            javascript=False,
            file_upload=True,
            file_download=True,
            screenshots=False,
            session_persistence=True,
            proxy_policy=True,
            authentication_mechanisms=("isolated_session_cookies",),
            version="1.0",
        )

    @property
    def browser_features(self) -> BrowserProviderFeatures:
        return self._features

    @property
    def descriptor(self) -> ProviderDescriptor:
        operations = tuple(operation.value for operation in self._features.operations)
        capability = Capability(
            name="browser.web",
            kind=CapabilityKind.TOOL,
            version=self._features.version,
            supported_operations=operations,
            features=("replaceable_provider", "session_isolation", "network_policy"),
            attributes=self._features.as_json(),
        )
        return ProviderDescriptor(
            provider_id=_PROVIDER_ID,
            provider_type="browser",
            supported_operations=operations,
            capabilities=(capability,),
            health=HealthStatus.HEALTHY,
            available=True,
            resources={"browser_features": self._features.as_json()},
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return browser_capability_registrations(self.descriptor.provider_id)

    async def get_session(
        self,
        session_id: str,
        context: OperationContext,
    ) -> BrowserSessionRef:
        state = self._get_state(session_id, context)
        return state.ref

    async def close_session(self, session_id: str, context: OperationContext) -> None:
        self._get_state(session_id, context)
        del self._sessions[session_id]

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        arguments = invocation.arguments_json()
        if invocation.tool_ref == NAVIGATE_TOOL_REF:
            output = await self._navigate(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == EXTRACT_TOOL_REF:
            output = await self._extract(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == FOLLOW_LINK_TOOL_REF:
            output = await self._follow_link(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == SUBMIT_FORM_TOOL_REF:
            output = await self._submit_form(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == DOWNLOAD_TOOL_REF:
            output, file_ref, artifact_ref = await self._download(arguments, invocation.context)
            return ToolResult(
                invocation_id=invocation.invocation_id,
                output=output,
                result_ref=file_ref,
                artifact_refs=(artifact_ref,),
                evidence_refs=(file_ref, artifact_ref),
            )
        if invocation.tool_ref == CLOSE_SESSION_TOOL_REF:
            session_id = _required_string(arguments, "session_id")
            await self.close_session(session_id, invocation.context)
            return ToolResult(
                invocation_id=invocation.invocation_id,
                output={"session_id": session_id, "closed": True},
            )
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"reference browser provider does not expose tool {invocation.tool_ref!r}",
            provider_id=self.descriptor.provider_id,
        )

    async def _navigate(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        url = _required_string(arguments, "url")
        state = self._session_for(arguments.get("session_id"), context)
        resource = await self._transport.fetch(state, url, BrowserOperation.NAVIGATE, context)
        _store_fetched_page(state, resource)
        return page_summary(state)

    async def _extract(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        session_id = _required_string(arguments, "session_id")
        state = self._get_state(session_id, context)
        parser = parse_page(state)
        find_value = arguments.get("find")
        find_text = find_value if isinstance(find_value, str) else None
        links: list[JsonValue] = [
            {
                "text": link.text,
                "href": urljoin(cast(str, state.current_url), link.href),
            }
            for link in parser.links
        ]
        output: dict[str, JsonValue] = {
            "session_id": session_id,
            "url": cast(str, state.current_url),
            "title": parser.title,
            "text": parser.text,
            "links": links,
            "matches": parser.text.casefold().count(find_text.casefold()) if find_text else 0,
            "content_type": state.content_type,
            "content_trust": CONTENT_TRUST,
        }
        if arguments.get("include_html") is True:
            output["html"] = decode_page(state.body or b"", state.charset)
        return output

    async def _follow_link(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        session_id = _required_string(arguments, "session_id")
        state = self._get_state(session_id, context)
        parser = parse_page(state)
        href_value = arguments.get("href")
        link_text_value = arguments.get("link_text")
        href: str | None = href_value if isinstance(href_value, str) else None
        if href is None and isinstance(link_text_value, str):
            expected = link_text_value.casefold()
            matching = [link for link in parser.links if link.text.casefold() == expected]
            if len(matching) != 1:
                raise ContractError(
                    ErrorCode.NOT_FOUND if not matching else ErrorCode.CONFLICT,
                    "browser link text must resolve to exactly one link",
                    provider_id=self.descriptor.provider_id,
                )
            href = matching[0].href
        if href is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "href or link_text is required")
        target = urljoin(cast(str, state.current_url), href)
        resource = await self._transport.fetch(
            state,
            target,
            BrowserOperation.FOLLOW_LINK,
            context,
        )
        _store_fetched_page(state, resource)
        return page_summary(state)

    async def _submit_form(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        session_id = _required_string(arguments, "session_id")
        state = self._get_state(session_id, context)
        parser = parse_page(state)
        form_index_value = arguments.get("form_index", 0)
        if not isinstance(form_index_value, int) or isinstance(form_index_value, bool):
            raise ContractError(ErrorCode.INVALID_REQUEST, "form_index must be an integer")
        try:
            form = parser.forms[form_index_value]
        except IndexError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "browser form index does not exist") from exc

        current_url = cast(str, state.current_url)
        target = urljoin(current_url, form.action or current_url)
        fields = dict(form.fields)
        requested_fields = arguments.get("fields")
        if requested_fields is not None:
            if not isinstance(requested_fields, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in requested_fields.items()
            ):
                raise ContractError(ErrorCode.INVALID_REQUEST, "form fields must be strings")
            fields.update(cast(dict[str, str], requested_fields))

        target, method, body, headers = await self._prepare_form_request(
            target=target,
            method=form.method.upper(),
            fields=fields,
            file_upload=arguments.get("file_upload"),
            context=context,
        )
        resource = await self._transport.fetch(
            state,
            target,
            BrowserOperation.SUBMIT_FORM,
            context,
            method=method,
            data=body,
            headers=headers,
        )
        _store_fetched_page(state, resource)
        return page_summary(state)

    async def _prepare_form_request(
        self,
        *,
        target: str,
        method: str,
        fields: dict[str, str],
        file_upload: JsonValue | None,
        context: OperationContext,
    ) -> tuple[str, str, bytes | None, dict[str, str]]:
        headers: dict[str, str] = {}
        if file_upload is not None:
            if method != "POST":
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "reference browser file upload supports POST forms only",
                )
            upload = _json_object(file_upload, "file_upload")
            file_ref = _required_string(upload, "file_ref")
            validate_id(file_ref, "file")
            file_bytes = await self._files.read(file_ref, context)
            body, content_type = _multipart_body(
                fields,
                field=_required_string(upload, "field"),
                filename=_required_string(upload, "filename"),
                file_content_type=(
                    cast(str, upload["content_type"])
                    if isinstance(upload.get("content_type"), str)
                    else "application/octet-stream"
                ),
                file_bytes=file_bytes,
            )
            headers["Content-Type"] = content_type
            return target, method, body, headers
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            return target, method, urlencode(fields).encode("utf-8"), headers
        if method == "GET":
            query = urlencode(fields)
            separator = "&" if "?" in target else "?"
            return (
                f"{target}{separator}{query}" if query else target,
                method,
                None,
                headers,
            )
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"reference browser does not support HTML form method {method!r}",
        )

    async def _download(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> tuple[dict[str, JsonValue], str, str]:
        if not isinstance(self._files, ArtifactLinkingFileProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "browser download requires a FileProvider with canonical artifact linking",
                provider_id=self.descriptor.provider_id,
            )
        url = _required_string(arguments, "url")
        state = self._session_for(arguments.get("session_id"), context)
        resource = await self._transport.fetch(state, url, BrowserOperation.DOWNLOAD, context)
        self._download_validation.validate(
            url=resource.final_url,
            content_type=resource.content_type,
            data=resource.data,
            context=context,
        )
        provenance_url = _redacted_url(resource.final_url)
        file_ref = new_id("file")
        digest = hashlib.sha256(resource.data).hexdigest()
        stored = await self._files.write(
            file_ref,
            resource.data,
            context,
            metadata={
                "source_url": provenance_url,
                "provenance": "browser_download",
                "content_type": resource.content_type,
                "sha256": digest,
                "downloaded_at": datetime.now(UTC).isoformat(),
                "content_trust": CONTENT_TRUST,
            },
        )
        artifact_ref = new_id("artifact")
        await self._files.link_artifact(
            stored.object_ref,
            artifact_ref,
            _data_access_context(context),
        )
        return (
            {
                "session_id": state.ref.session_id,
                "file_ref": stored.object_ref,
                "artifact_ref": artifact_ref,
                "source_url": provenance_url,
                "content_type": resource.content_type,
                "size_bytes": len(resource.data),
                "sha256": digest,
                "content_trust": CONTENT_TRUST,
            },
            stored.object_ref,
            artifact_ref,
        )

    def _session_for(
        self,
        session_value: JsonValue | None,
        context: OperationContext,
    ) -> SessionState:
        self._evict_expired_sessions()
        if isinstance(session_value, str):
            return self._get_state(session_value, context)
        ref = BrowserSessionRef.create(
            context,
            privacy=BrowserPrivacyClassification.STANDARD,
            allowed_domains=self._network_policy.allowed_domains,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._session_ttl_seconds),
        )
        state = SessionState(ref=ref)
        self._sessions[ref.session_id] = state
        return state

    def _get_state(self, session_id: str, context: OperationContext) -> SessionState:
        validate_id(session_id, "browser_session")
        self._evict_expired_sessions()
        try:
            state = self._sessions[session_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"browser session not found or expired: {session_id}"
            ) from exc
        scope = state.ref.scope
        if scope.owner_type != context.owner_type or scope.owner_id != context.owner_id:
            raise ContractError(ErrorCode.FORBIDDEN, "browser session belongs to another owner")
        if scope.project_id != context.project_id:
            raise ContractError(ErrorCode.FORBIDDEN, "browser session belongs to another project")
        return state

    def _evict_expired_sessions(self) -> None:
        now = datetime.now(UTC)
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if state.ref.expires_at is not None and state.ref.expires_at <= now
        ]
        for session_id in expired:
            del self._sessions[session_id]


def _store_fetched_page(state: SessionState, resource: FetchedResource) -> None:
    store_page(
        state,
        final_url=resource.final_url,
        data=resource.data,
        content_type=resource.content_type,
        charset=resource.charset,
        status_code=resource.status_code,
    )


def _redacted_url(url: str) -> str:
    """Remove credentials, query parameters and fragments from persisted browser URLs."""

    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "browser response URL has no hostname")
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if parsed.port is None else f"{display_host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))


def _data_access_context(operation: OperationContext) -> DataAccessContext:
    if operation.owner_type is not None and operation.owner_id is not None:
        actor_ref = f"{operation.owner_type}:{operation.owner_id}"
    else:
        actor_ref = "service:platform"
    return DataAccessContext(operation=operation, actor_ref=actor_ref)


def _required_string(arguments: dict[str, JsonValue], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-empty string")
    return value


def _json_object(value: JsonValue, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an object")
    return value


def _multipart_body(
    fields: dict[str, str],
    *,
    field: str,
    filename: str,
    file_content_type: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    if "\r" in file_content_type or "\n" in file_content_type:
        raise ContractError(ErrorCode.INVALID_REQUEST, "file content_type contains line breaks")
    boundary = f"----ai-multi-agent-platform-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{_quote_header(name)}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                "Content-Disposition: form-data; "
                f'name="{_quote_header(field)}"; filename="{_quote_header(filename)}"\r\n'
            ).encode(),
            f"Content-Type: {file_content_type}\r\n\r\n".encode(),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _quote_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")
