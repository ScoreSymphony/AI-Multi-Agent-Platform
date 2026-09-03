from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.browser import (
    BROWSER_DOWNLOAD_CAPABILITY_ID,
    BROWSER_EXTRACT_CAPABILITY_ID,
    BROWSER_FOLLOW_LINK_CAPABILITY_ID,
    BROWSER_NAVIGATE_CAPABILITY_ID,
    BROWSER_SUBMIT_FORM_CAPABILITY_ID,
    BrowserNetworkPolicy,
    StdlibBrowserProvider,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    CapabilitySpec,
    InvocationRecord,
    InvocationTrace,
    PolicyDecision,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    JsonValue,
    OperationContext,
    OperationControl,
    ProviderDescriptor,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import new_id


class _FixtureHTTPServer(ThreadingHTTPServer):
    last_post_body: bytes = b""
    last_post_content_type: str = ""
    post_count: int = 0


class _Handler(BaseHTTPRequestHandler):
    server: _FixtureHTTPServer

    def do_GET(self) -> None:
        if self.path == "/":
            self._write_html(
                """<!doctype html><html><head><title>Root</title></head><body>
                <p>alpha beta alpha</p>
                <a href="/next">Next page</a>
                <form action="/submit" method="post">
                  <input name="existing" value="default">
                  <input name="upload" type="file">
                </form>
                </body></html>"""
            )
            return
        if self.path == "/next":
            self._write_html("<html><head><title>Next</title></head><body>next body</body></html>")
            return
        if self.path == "/file":
            payload = b"download-me"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/slow":
            time.sleep(0.15)
            try:
                self._write_html("<html><head><title>Slow</title></head><body>slow</body></html>")
            except BrokenPipeError:
                pass
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/submit":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.server.last_post_body = self.rfile.read(length)
        self.server.last_post_content_type = self.headers.get("Content-Type", "")
        self.server.post_count += 1
        self._write_html("<html><head><title>Submitted</title></head><body>submitted</body></html>")

    def _write_html(self, html: str) -> None:
        payload = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


class _FixtureServer:
    def __init__(self) -> None:
        self.server = _FixtureHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _FixtureServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"


class RecordingObserver:
    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    async def record(self, record: InvocationRecord) -> None:
        self.records.append(record)


def _operation(*, project_id: str | None = None, timeout: float | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="browser-correlation",
        owner_type="user",
        owner_id="user-1",
        project_id=project_id or new_id("project"),
        control=OperationControl(timeout_seconds=timeout),
    )


def _request(
    capability_id: str,
    arguments: dict[str, JsonValue],
    *,
    context: OperationContext,
    permissions: frozenset[str],
    invocation_id: str,
) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id=capability_id,
        arguments=arguments,
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=context.project_id,
        ),
        granted_permissions=permissions,
    )


def _provider(
    tmp_path: Path, *, allow_private: bool = True
) -> tuple[StdlibBrowserProvider, LocalFileProvider]:
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
    provider = StdlibBrowserProvider(
        files,
        network_policy=BrowserNetworkPolicy(
            allowed_domains=("127.0.0.1",),
            allow_private_networks=allow_private,
        ),
    )
    return provider, files


def test_navigation_extract_follow_and_trace_preservation(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _FixtureServer() as fixture:
            provider, _ = _provider(tmp_path)
            registry = CapabilityRegistry()
            await registry.register_provider(provider)
            observer = RecordingObserver()
            invoker = CapabilityInvoker(registry, observer=observer)
            context = _operation()

            navigation = await invoker.invoke(
                _request(
                    BROWSER_NAVIGATE_CAPABILITY_ID,
                    {"url": fixture.base_url + "/"},
                    context=context,
                    permissions=frozenset({"browser.network.read"}),
                    invocation_id="browser-nav",
                )
            )
            output = navigation.output
            assert isinstance(output, dict)
            session_id = output["session_id"]
            assert isinstance(session_id, str) and session_id.startswith("browser_session_")
            assert output["title"] == "Root"
            assert output["content_trust"] == "untrusted_web_content"

            extracted = await invoker.invoke(
                _request(
                    BROWSER_EXTRACT_CAPABILITY_ID,
                    {"session_id": session_id, "find": "alpha"},
                    context=context,
                    permissions=frozenset({"browser.content.read"}),
                    invocation_id="browser-extract",
                )
            )
            extracted_output = extracted.output
            assert isinstance(extracted_output, dict)
            assert extracted_output["matches"] == 2
            assert "alpha beta alpha" in str(extracted_output["text"])
            assert extracted_output["content_trust"] == "untrusted_web_content"

            followed = await invoker.invoke(
                _request(
                    BROWSER_FOLLOW_LINK_CAPABILITY_ID,
                    {"session_id": session_id, "link_text": "Next page"},
                    context=context,
                    permissions=frozenset({"browser.network.read"}),
                    invocation_id="browser-follow",
                )
            )
            followed_output = followed.output
            assert isinstance(followed_output, dict)
            assert followed_output["title"] == "Next"
            assert followed_output["session_id"] == session_id

            assert observer.records[-1].trace.correlation_id == context.correlation_id
            assert observer.records[-1].trace.project_id == context.project_id
            assert provider.browser_features.javascript is False
            assert provider.browser_features.screenshots is False

    asyncio.run(scenario())


def test_download_enters_canonical_file_and_artifact_path_with_provenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _FixtureServer() as fixture:
            provider, files = _provider(tmp_path)
            registry = CapabilityRegistry()
            await registry.register_provider(provider)
            context = _operation()
            result = await CapabilityInvoker(registry).invoke(
                _request(
                    BROWSER_DOWNLOAD_CAPABILITY_ID,
                    {"url": fixture.base_url + "/file"},
                    context=context,
                    permissions=frozenset(
                        {"browser.network.read", "file.create", "artifact.create"}
                    ),
                    invocation_id="browser-download",
                )
            )
            output = result.output
            assert isinstance(output, dict)
            file_ref = output["file_ref"]
            artifact_ref = output["artifact_ref"]
            assert isinstance(file_ref, str) and file_ref.startswith("file_")
            assert isinstance(artifact_ref, str) and artifact_ref.startswith("artifact_")
            assert result.result_ref == file_ref
            assert result.artifact_refs == (artifact_ref,)
            assert result.evidence_refs == (file_ref, artifact_ref)
            assert await files.read(file_ref, context) == b"download-me"
            record = await files.get_file(
                file_ref,
                DataAccessContext(operation=context, actor_ref="user:user-1"),
            )
            assert record.sha256 == hashlib.sha256(b"download-me").hexdigest()
            assert record.artifact_ids == (artifact_ref,)
            assert record.metadata["source_url"] == fixture.base_url + "/file"
            assert record.metadata["provenance"] == "browser_download"
            assert record.metadata["content_trust"] == "untrusted_web_content"

    asyncio.run(scenario())


def test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        with _FixtureServer() as fixture:
            provider, files = _provider(tmp_path)
            registry = CapabilityRegistry()
            await registry.register_provider(provider)
            context = _operation()
            invoker = CapabilityInvoker(registry)
            navigation = await invoker.invoke(
                _request(
                    BROWSER_NAVIGATE_CAPABILITY_ID,
                    {"url": fixture.base_url + "/"},
                    context=context,
                    permissions=frozenset({"browser.network.read"}),
                    invocation_id="browser-form-nav",
                )
            )
            nav_output = navigation.output
            assert isinstance(nav_output, dict)
            session_id = nav_output["session_id"]
            assert isinstance(session_id, str)
            upload_ref = new_id("file")
            await files.write(upload_ref, b"upload-data", context)
            upload_args: dict[str, JsonValue] = {
                "session_id": session_id,
                "fields": {"name": "Ada"},
                "file_upload": {
                    "field": "upload",
                    "file_ref": upload_ref,
                    "filename": "example.txt",
                    "content_type": "text/plain",
                },
            }

            with pytest.raises(ContractError) as missing_file_read:
                await invoker.invoke(
                    _request(
                        BROWSER_SUBMIT_FORM_CAPABILITY_ID,
                        upload_args,
                        context=context,
                        permissions=frozenset({"browser.external.submit"}),
                        invocation_id="browser-form-missing-file-read",
                    )
                )
            assert missing_file_read.value.code is ErrorCode.FORBIDDEN
            assert fixture.server.post_count == 0

            async def deny_external(
                request: CapabilityInvocation,
                capability: CapabilitySpec,
            ) -> PolicyDecision:
                del request
                if capability.side_effects.value == "external":
                    return PolicyDecision.DENY
                return PolicyDecision.ALLOW

            denied = CapabilityInvoker(registry, policy_hook=deny_external)
            with pytest.raises(ContractError) as caught:
                await denied.invoke(
                    _request(
                        BROWSER_SUBMIT_FORM_CAPABILITY_ID,
                        upload_args,
                        context=context,
                        permissions=frozenset({"browser.external.submit", "file.read"}),
                        invocation_id="browser-form-denied",
                    )
                )
            assert caught.value.code is ErrorCode.FORBIDDEN
            assert fixture.server.post_count == 0

            allowed = await invoker.invoke(
                _request(
                    BROWSER_SUBMIT_FORM_CAPABILITY_ID,
                    upload_args,
                    context=context,
                    permissions=frozenset({"browser.external.submit", "file.read"}),
                    invocation_id="browser-form-allowed",
                )
            )
            allowed_output = allowed.output
            assert isinstance(allowed_output, dict)
            assert allowed_output["title"] == "Submitted"
            assert fixture.server.post_count == 1
            assert b"upload-data" in fixture.server.last_post_body
            assert b"Ada" in fixture.server.last_post_body
            assert fixture.server.last_post_content_type.startswith("multipart/form-data;")

    asyncio.run(scenario())


def test_network_policy_blocks_private_target(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _FixtureServer() as fixture:
            provider, _ = _provider(tmp_path, allow_private=False)
            registry = CapabilityRegistry()
            await registry.register_provider(provider)
            context = _operation()
            with pytest.raises(ContractError) as caught:
                await CapabilityInvoker(registry).invoke(
                    _request(
                        BROWSER_NAVIGATE_CAPABILITY_ID,
                        {"url": fixture.base_url + "/"},
                        context=context,
                        permissions=frozenset({"browser.network.read"}),
                        invocation_id="browser-private-block",
                    )
                )
            assert caught.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_session_isolation_by_project(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _FixtureServer() as fixture:
            provider, _ = _provider(tmp_path)
            registry = CapabilityRegistry()
            await registry.register_provider(provider)
            project_a = new_id("project")
            context_a = _operation(project_id=project_a)
            nav = await CapabilityInvoker(registry).invoke(
                _request(
                    BROWSER_NAVIGATE_CAPABILITY_ID,
                    {"url": fixture.base_url + "/"},
                    context=context_a,
                    permissions=frozenset({"browser.network.read"}),
                    invocation_id="browser-isolation-nav",
                )
            )
            output = nav.output
            assert isinstance(output, dict)
            session_id = output["session_id"]
            assert isinstance(session_id, str)

            context_b = _operation(project_id=new_id("project"))
            with pytest.raises(ContractError) as caught:
                await CapabilityInvoker(registry).invoke(
                    _request(
                        BROWSER_EXTRACT_CAPABILITY_ID,
                        {"session_id": session_id},
                        context=context_b,
                        permissions=frozenset({"browser.content.read"}),
                        invocation_id="browser-isolation-read",
                    )
                )
            assert caught.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_timeout_and_cancellation_use_canonical_errors(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _FixtureServer() as fixture:
            provider, _ = _provider(tmp_path)
            registry = CapabilityRegistry()
            await registry.register_provider(provider)

            timeout_context = _operation(timeout=0.01)
            with pytest.raises(ContractError) as timeout_error:
                await CapabilityInvoker(registry).invoke(
                    _request(
                        BROWSER_NAVIGATE_CAPABILITY_ID,
                        {"url": fixture.base_url + "/slow"},
                        context=timeout_context,
                        permissions=frozenset({"browser.network.read"}),
                        invocation_id="browser-timeout",
                    )
                )
            assert timeout_error.value.code is ErrorCode.TIMEOUT

            cancel_context = _operation(timeout=1.0)
            pending = asyncio.create_task(
                CapabilityInvoker(registry).invoke(
                    _request(
                        BROWSER_NAVIGATE_CAPABILITY_ID,
                        {"url": fixture.base_url + "/slow"},
                        context=cancel_context,
                        permissions=frozenset({"browser.network.read"}),
                        invocation_id="browser-cancel",
                    )
                )
            )
            await asyncio.sleep(0.01)
            pending.cancel()
            with pytest.raises(ContractError) as cancellation_error:
                await pending
            assert cancellation_error.value.code is ErrorCode.CANCELLED

    asyncio.run(scenario())


def test_provider_replacement_unsupported_operation_and_disabled_path(tmp_path: Path) -> None:
    class AlternateBrowserProvider(StdlibBrowserProvider):
        @property
        def descriptor(self) -> ProviderDescriptor:
            return replace(super().descriptor, provider_id="browser.alternate.reference")

    async def scenario() -> None:
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        empty = CapabilityRegistry()
        with pytest.raises(ContractError) as missing:
            empty.resolve(BROWSER_NAVIGATE_CAPABILITY_ID)
        assert missing.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

        provider = AlternateBrowserProvider(
            files,
            network_policy=BrowserNetworkPolicy(
                allowed_domains=("127.0.0.1",), allow_private_networks=True
            ),
        )
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        assert provider.browser_features.screenshots is False
        with pytest.raises(ContractError) as unsupported:
            registry.resolve("browser.screenshot")
        assert unsupported.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

        with _FixtureServer() as fixture:
            result = await CapabilityInvoker(registry).invoke(
                _request(
                    BROWSER_NAVIGATE_CAPABILITY_ID,
                    {"url": fixture.base_url + "/"},
                    context=_operation(),
                    permissions=frozenset({"browser.network.read"}),
                    invocation_id="browser-alt",
                )
            )
            assert result.provider_id == "browser.alternate.reference"

        registry.unregister_provider("browser.alternate.reference")
        with pytest.raises(ContractError) as removed:
            registry.resolve(BROWSER_NAVIGATE_CAPABILITY_ID)
        assert removed.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    asyncio.run(scenario())
