"""Small self-hosted browser reference adapter using only the Python standard library.

The adapter intentionally does not implement JavaScript or screenshots. Those features remain
provider metadata so a Playwright/CDP/remote-browser implementation can replace this adapter
without changing canonical capability requests.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    Request,
    build_opener,
)
from uuid import uuid4

from ai_multi_agent_platform.capabilities.types import (
    CapabilityRegistration,
    CapabilitySpec,
    SafetyClassification,
    SideEffectClassification,
)
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

BROWSER_NAVIGATE_CAPABILITY_ID = "browser.navigate"
BROWSER_EXTRACT_CAPABILITY_ID = "browser.extract"
BROWSER_FOLLOW_LINK_CAPABILITY_ID = "browser.follow_link"
BROWSER_SUBMIT_FORM_CAPABILITY_ID = "browser.submit_form"
BROWSER_DOWNLOAD_CAPABILITY_ID = "browser.download"
BROWSER_CLOSE_SESSION_CAPABILITY_ID = "browser.close_session"

_NAVIGATE_TOOL_REF = "browser.reference.navigate"
_EXTRACT_TOOL_REF = "browser.reference.extract"
_FOLLOW_LINK_TOOL_REF = "browser.reference.follow_link"
_SUBMIT_FORM_TOOL_REF = "browser.reference.submit_form"
_DOWNLOAD_TOOL_REF = "browser.reference.download"
_CLOSE_SESSION_TOOL_REF = "browser.reference.close_session"

CONTENT_TRUST = "untrusted_web_content"


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


@dataclass(slots=True)
class _Link:
    href: str
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


@dataclass(slots=True)
class _Form:
    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[_Link] = []
        self.forms: list[_Form] = []
        self._in_title = False
        self._current_link: _Link | None = None
        self._current_form: _Form | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value for name, value in attrs}
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        elif lowered == "a":
            href = attributes.get("href")
            if href:
                link = _Link(href=href)
                self.links.append(link)
                self._current_link = link
        elif lowered == "form":
            form = _Form(
                action=attributes.get("action") or "",
                method=(attributes.get("method") or "GET").upper(),
            )
            self.forms.append(form)
            self._current_form = form
        elif lowered == "input" and self._current_form is not None:
            name = attributes.get("name")
            if name:
                input_type = (attributes.get("type") or "text").lower()
                if input_type not in {"file", "submit", "button", "image"}:
                    self._current_form.fields[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        elif lowered == "a":
            self._current_link = None
        elif lowered == "form":
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._current_link is not None:
            self._current_link.text_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


@dataclass(slots=True)
class _SessionState:
    ref: BrowserSessionRef
    cookies: CookieJar = field(default_factory=CookieJar)
    current_url: str | None = None
    body: bytes | None = None
    content_type: str | None = None
    charset: str = "utf-8"
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class _FetchedResource:
    final_url: str
    status_code: int
    content_type: str | None
    charset: str
    data: bytes


class _PolicyRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        hook: BrowserNetworkPolicyHook,
        operation: BrowserOperation,
        context: OperationContext,
    ) -> None:
        super().__init__()
        self._hook = hook
        self._operation = operation
        self._context = context

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        self._hook.check(newurl, self._operation, self._context)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        self._files = file_provider
        self._network_policy = network_policy or BrowserNetworkPolicy()
        self._network_hook = network_policy_hook or DefaultBrowserNetworkPolicyHook(
            self._network_policy
        )
        self._download_validation = download_validation_hook or DefaultDownloadValidationHook()
        self._request_timeout_seconds = request_timeout_seconds
        self._sessions: dict[str, _SessionState] = {}
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
            provider_id="browser.stdlib.reference",
            provider_type="browser",
            supported_operations=operations,
            capabilities=(capability,),
            health=HealthStatus.HEALTHY,
            available=True,
            resources={"browser_features": self._features.as_json()},
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        common_output: dict[str, JsonValue] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "url": {"type": "string"},
                "status_code": {"type": "integer"},
                "title": {"type": "string"},
                "content_type": {"type": ["string", "null"]},
                "size_bytes": {"type": "integer", "minimum": 0},
                "content_trust": {"const": CONTENT_TRUST},
            },
            "required": [
                "session_id",
                "url",
                "status_code",
                "title",
                "content_type",
                "size_bytes",
                "content_trust",
            ],
            "additionalProperties": False,
        }
        specs = (
            (
                CapabilitySpec(
                    capability_id=BROWSER_NAVIGATE_CAPABILITY_ID,
                    name="Navigate browser",
                    description="Open a HTTP(S) URL in an isolated canonical browser session.",
                    version="1.0",
                    input_schema=_object_schema(
                        {
                            "url": {"type": "string", "minLength": 1},
                            "session_id": {"type": "string", "minLength": 1},
                        },
                        required=("url",),
                    ),
                    output_schema=common_output,
                    tags=("browser", "web", "read"),
                    side_effects=SideEffectClassification.NONE,
                    required_permissions=("browser.network.read",),
                    health=HealthStatus.HEALTHY,
                    features=("http", "https", "cookies", "session_reuse"),
                ),
                _NAVIGATE_TOOL_REF,
            ),
            (
                CapabilitySpec(
                    capability_id=BROWSER_EXTRACT_CAPABILITY_ID,
                    name="Extract browser page",
                    description="Extract untrusted text/link metadata from the current page.",
                    version="1.0",
                    input_schema=_object_schema(
                        {
                            "session_id": {"type": "string", "minLength": 1},
                            "find": {"type": "string"},
                            "include_html": {"type": "boolean"},
                        },
                        required=("session_id",),
                    ),
                    output_schema={"type": "object"},
                    tags=("browser", "web", "extract", "untrusted-content"),
                    side_effects=SideEffectClassification.NONE,
                    required_permissions=("browser.content.read",),
                    health=HealthStatus.HEALTHY,
                    features=("text", "links", "find", "html"),
                ),
                _EXTRACT_TOOL_REF,
            ),
            (
                CapabilitySpec(
                    capability_id=BROWSER_FOLLOW_LINK_CAPABILITY_ID,
                    name="Follow browser link",
                    description="Follow one href or visible link text in the current page.",
                    version="1.0",
                    input_schema={
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string", "minLength": 1},
                            "href": {"type": "string", "minLength": 1},
                            "link_text": {"type": "string", "minLength": 1},
                        },
                        "required": ["session_id"],
                        "oneOf": [{"required": ["href"]}, {"required": ["link_text"]}],
                        "additionalProperties": False,
                    },
                    output_schema=common_output,
                    tags=("browser", "web", "read"),
                    side_effects=SideEffectClassification.NONE,
                    required_permissions=("browser.network.read",),
                    health=HealthStatus.HEALTHY,
                ),
                _FOLLOW_LINK_TOOL_REF,
            ),
            (
                CapabilitySpec(
                    capability_id=BROWSER_SUBMIT_FORM_CAPABILITY_ID,
                    name="Submit browser form",
                    description=(
                        "Submit a HTML form, optionally uploading one authorized canonical file."
                    ),
                    version="1.0",
                    input_schema=_object_schema(
                        {
                            "session_id": {"type": "string", "minLength": 1},
                            "form_index": {"type": "integer", "minimum": 0},
                            "fields": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                            "file_upload": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string", "minLength": 1},
                                    "file_ref": {"type": "string", "minLength": 1},
                                    "filename": {"type": "string", "minLength": 1},
                                    "content_type": {"type": "string", "minLength": 1},
                                },
                                "required": ["field", "file_ref", "filename"],
                                "additionalProperties": False,
                            },
                        },
                        required=("session_id",),
                    ),
                    output_schema=common_output,
                    tags=("browser", "web", "external-side-effect", "upload"),
                    safety=SafetyClassification.RESTRICTED,
                    side_effects=SideEffectClassification.EXTERNAL,
                    required_permissions=("browser.external.submit",),
                    health=HealthStatus.HEALTHY,
                    features=("forms", "canonical_file_upload"),
                ),
                _SUBMIT_FORM_TOOL_REF,
            ),
            (
                CapabilitySpec(
                    capability_id=BROWSER_DOWNLOAD_CAPABILITY_ID,
                    name="Download browser file",
                    description=(
                        "Download HTTP(S) content into canonical FileProvider storage "
                        "with provenance."
                    ),
                    version="1.0",
                    input_schema=_object_schema(
                        {
                            "url": {"type": "string", "minLength": 1},
                            "session_id": {"type": "string", "minLength": 1},
                        },
                        required=("url",),
                    ),
                    output_schema={"type": "object"},
                    tags=("browser", "web", "download", "file"),
                    safety=SafetyClassification.RESTRICTED,
                    side_effects=SideEffectClassification.LOCAL_WRITE,
                    required_permissions=("browser.network.read", "file.create"),
                    health=HealthStatus.HEALTHY,
                    features=("canonical_file_download", "sha256", "provenance"),
                ),
                _DOWNLOAD_TOOL_REF,
            ),
            (
                CapabilitySpec(
                    capability_id=BROWSER_CLOSE_SESSION_CAPABILITY_ID,
                    name="Close browser session",
                    description="Close an isolated canonical browser session.",
                    version="1.0",
                    input_schema=_object_schema(
                        {"session_id": {"type": "string", "minLength": 1}},
                        required=("session_id",),
                    ),
                    output_schema={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "closed": {"const": True},
                        },
                        "required": ["session_id", "closed"],
                        "additionalProperties": False,
                    },
                    tags=("browser", "session"),
                    side_effects=SideEffectClassification.NONE,
                    required_permissions=("browser.session.manage",),
                    health=HealthStatus.HEALTHY,
                ),
                _CLOSE_SESSION_TOOL_REF,
            ),
        )
        return tuple(
            CapabilityRegistration(
                capability=spec,
                provider_id=self.descriptor.provider_id,
                provider_tool_ref=tool_ref,
                priority=100,
            )
            for spec, tool_ref in specs
        )

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
        if invocation.tool_ref == _NAVIGATE_TOOL_REF:
            output = await self._navigate(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == _EXTRACT_TOOL_REF:
            output = await self._extract(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == _FOLLOW_LINK_TOOL_REF:
            output = await self._follow_link(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == _SUBMIT_FORM_TOOL_REF:
            output = await self._submit_form(arguments, invocation.context)
            return ToolResult(invocation_id=invocation.invocation_id, output=output)
        if invocation.tool_ref == _DOWNLOAD_TOOL_REF:
            output, file_ref = await self._download(arguments, invocation.context)
            return ToolResult(
                invocation_id=invocation.invocation_id,
                output=output,
                result_ref=file_ref,
                evidence_refs=(file_ref,),
            )
        if invocation.tool_ref == _CLOSE_SESSION_TOOL_REF:
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
        resource = await self._fetch(state, url, BrowserOperation.NAVIGATE, context)
        self._store_page(state, resource)
        return self._page_summary(state)

    async def _extract(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        session_id = _required_string(arguments, "session_id")
        state = self._get_state(session_id, context)
        parser = self._parse_page(state)
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
            output["html"] = self._decode(state.body or b"", state.charset)
        return output

    async def _follow_link(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        session_id = _required_string(arguments, "session_id")
        state = self._get_state(session_id, context)
        parser = self._parse_page(state)
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
        resource = await self._fetch(state, target, BrowserOperation.FOLLOW_LINK, context)
        self._store_page(state, resource)
        return self._page_summary(state)

    async def _submit_form(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> dict[str, JsonValue]:
        session_id = _required_string(arguments, "session_id")
        state = self._get_state(session_id, context)
        parser = self._parse_page(state)
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

        file_upload = arguments.get("file_upload")
        headers: dict[str, str] = {}
        method = form.method.upper()
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
        elif method == "POST":
            body = urlencode(fields).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif method == "GET":
            query = urlencode(fields)
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}{query}" if query else target
            body = None
        else:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"reference browser does not support HTML form method {method!r}",
            )

        resource = await self._fetch(
            state,
            target,
            BrowserOperation.SUBMIT_FORM,
            context,
            method=method,
            data=body,
            headers=headers,
        )
        self._store_page(state, resource)
        return self._page_summary(state)

    async def _download(
        self,
        arguments: dict[str, JsonValue],
        context: OperationContext,
    ) -> tuple[dict[str, JsonValue], str]:
        url = _required_string(arguments, "url")
        state = self._session_for(arguments.get("session_id"), context)
        resource = await self._fetch(state, url, BrowserOperation.DOWNLOAD, context)
        self._download_validation.validate(
            url=resource.final_url,
            content_type=resource.content_type,
            data=resource.data,
            context=context,
        )
        file_ref = new_id("file")
        digest = hashlib.sha256(resource.data).hexdigest()
        stored = await self._files.write(
            file_ref,
            resource.data,
            context,
            metadata={
                "source_url": resource.final_url,
                "provenance": "browser_download",
                "content_type": resource.content_type,
                "sha256": digest,
                "downloaded_at": datetime.now(UTC).isoformat(),
                "content_trust": CONTENT_TRUST,
            },
        )
        return (
            {
                "session_id": state.ref.session_id,
                "file_ref": stored.object_ref,
                "source_url": resource.final_url,
                "content_type": resource.content_type,
                "size_bytes": len(resource.data),
                "sha256": digest,
                "content_trust": CONTENT_TRUST,
            },
            stored.object_ref,
        )

    async def _fetch(
        self,
        state: _SessionState,
        url: str,
        operation: BrowserOperation,
        context: OperationContext,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FetchedResource:
        self._network_hook.check(url, operation, context)
        timeout = context.control.timeout_seconds or self._request_timeout_seconds
        return await asyncio.to_thread(
            self._fetch_sync,
            state,
            url,
            operation,
            context,
            method,
            data,
            headers or {},
            timeout,
        )

    def _fetch_sync(
        self,
        state: _SessionState,
        url: str,
        operation: BrowserOperation,
        context: OperationContext,
        method: str,
        data: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> _FetchedResource:
        opener = build_opener(
            HTTPCookieProcessor(state.cookies),
            _PolicyRedirectHandler(self._network_hook, operation, context),
        )
        request = Request(url=url, data=data, headers=headers, method=method)
        response: Any
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        except TimeoutError as exc:
            raise ContractError(
                ErrorCode.TIMEOUT,
                "reference browser network request timed out",
                provider_id=self.descriptor.provider_id,
                retryable=True,
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ContractError(
                    ErrorCode.TIMEOUT,
                    "reference browser network request timed out",
                    provider_id=self.descriptor.provider_id,
                    retryable=True,
                ) from exc
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "reference browser network request failed",
                provider_id=self.descriptor.provider_id,
                retryable=True,
            ) from exc
        except OSError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "reference browser network request failed",
                provider_id=self.descriptor.provider_id,
                retryable=True,
            ) from exc

        try:
            final_url = str(response.geturl())
            self._network_hook.check(final_url, operation, context)
            data_bytes = response.read(self._network_policy.max_response_bytes + 1)
            if len(data_bytes) > self._network_policy.max_response_bytes:
                raise ContractError(
                    ErrorCode.INPUT_TOO_LARGE,
                    "browser response exceeds configured maximum size",
                    provider_id=self.descriptor.provider_id,
                )
            response_headers = cast(Message, response.headers)
            content_type = response_headers.get_content_type() if response_headers else None
            charset = response_headers.get_content_charset() if response_headers else None
            return _FetchedResource(
                final_url=final_url,
                status_code=int(response.getcode()),
                content_type=content_type,
                charset=charset or "utf-8",
                data=data_bytes,
            )
        finally:
            response.close()

    def _session_for(
        self,
        session_value: JsonValue | None,
        context: OperationContext,
    ) -> _SessionState:
        if isinstance(session_value, str):
            return self._get_state(session_value, context)
        ref = BrowserSessionRef.create(
            context,
            privacy=BrowserPrivacyClassification.STANDARD,
            allowed_domains=self._network_policy.allowed_domains,
        )
        state = _SessionState(ref=ref)
        self._sessions[ref.session_id] = state
        return state

    def _get_state(self, session_id: str, context: OperationContext) -> _SessionState:
        validate_id(session_id, "browser_session")
        try:
            state = self._sessions[session_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"browser session not found: {session_id}"
            ) from exc
        ref = state.ref
        if ref.expires_at is not None and ref.expires_at <= datetime.now(UTC):
            del self._sessions[session_id]
            raise ContractError(ErrorCode.NOT_FOUND, f"browser session expired: {session_id}")
        scope = ref.scope
        if scope.owner_type != context.owner_type or scope.owner_id != context.owner_id:
            raise ContractError(ErrorCode.FORBIDDEN, "browser session belongs to another owner")
        if scope.project_id != context.project_id:
            raise ContractError(ErrorCode.FORBIDDEN, "browser session belongs to another project")
        return state

    def _store_page(self, state: _SessionState, resource: _FetchedResource) -> None:
        state.current_url = resource.final_url
        state.body = resource.data
        state.content_type = resource.content_type
        state.charset = resource.charset
        state.status_code = resource.status_code

    def _parse_page(self, state: _SessionState) -> _PageParser:
        if state.current_url is None or state.body is None:
            raise ContractError(ErrorCode.NOT_FOUND, "browser session has no current page")
        parser = _PageParser()
        parser.feed(self._decode(state.body, state.charset))
        parser.close()
        return parser

    def _page_summary(self, state: _SessionState) -> dict[str, JsonValue]:
        parser = self._parse_page(state)
        return {
            "session_id": state.ref.session_id,
            "url": cast(str, state.current_url),
            "status_code": cast(int, state.status_code),
            "title": parser.title,
            "content_type": state.content_type,
            "size_bytes": len(state.body or b""),
            "content_trust": CONTENT_TRUST,
        }

    @staticmethod
    def _decode(data: bytes, charset: str) -> str:
        try:
            return data.decode(charset, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")


def _object_schema(
    properties: Mapping[str, JsonValue],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


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
