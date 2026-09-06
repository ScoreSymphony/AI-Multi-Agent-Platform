"""Private HTTP transport for the existing authenticated Worker protocol.

This module does not create a second Worker API or authentication model. It only
serializes the #14 ``WorkerProtocolService`` boundary so Worker processes can
register and heartbeat across a host boundary. Worker credentials remain HTTP
transport metadata and are never embedded in canonical Node/Worker payloads.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from ai_multi_agent_platform.contracts import AdapterMetadata
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import AuthenticationError, AuthenticationFailure

from .models import (
    WORKER_PROTOCOL_VERSION,
    AcceleratorResource,
    Heartbeat,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
)
from .registry import RegistryError
from .worker_protocol import (
    WorkerHeartbeatRequest,
    WorkerProtocolAuthorizationError,
    WorkerProtocolError,
    WorkerProtocolReceipt,
    WorkerProtocolService,
    WorkerRequestCredentials,
)

WORKER_PROTOCOL_HTTP_PREFIX = "/internal/worker-protocol/v1"
_DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class WorkerProtocolHTTPRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", self.method.upper())
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        object.__setattr__(self, "body", MappingProxyType(dict(self.body)))


@dataclass(frozen=True, slots=True)
class WorkerProtocolHTTPResponse:
    status: int
    body: JsonValue
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class WorkerProtocolHTTPClientError(RuntimeError):
    """Sanitized Worker-side network/protocol error."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


class WorkerProtocolCodec:
    """JSON wire codec for Worker-owned registration and liveness reports."""

    @staticmethod
    def encode_registration(request: RegistrationRequest) -> dict[str, JsonValue]:
        return {
            "node": _encode_node(request.node),
            "workers": [_encode_worker(worker) for worker in request.workers],
            "protocol_version": request.protocol_version,
            "service_identity_ref": request.service_identity_ref,
        }

    @staticmethod
    def decode_registration(value: object) -> RegistrationRequest:
        data = _mapping(value, "registration")
        workers_raw = _array(_required(data, "workers"), "workers")
        return RegistrationRequest(
            node=_decode_node(_required(data, "node")),
            workers=tuple(_decode_worker(item) for item in workers_raw),
            protocol_version=_string(_required(data, "protocol_version"), "protocol_version"),
            service_identity_ref=_optional_string(
                data.get("service_identity_ref"), "service_identity_ref"
            ),
        )

    @staticmethod
    def encode_heartbeat(request: WorkerHeartbeatRequest) -> dict[str, JsonValue]:
        heartbeat = request.heartbeat
        return {
            "heartbeat": {
                "node_id": heartbeat.node_id,
                "observed_at": heartbeat.observed_at.astimezone(UTC).isoformat(),
                "sequence": heartbeat.sequence,
                "resources": (
                    None if heartbeat.resources is None else _encode_resources(heartbeat.resources)
                ),
                "node_status": (
                    None if heartbeat.node_status is None else heartbeat.node_status.value
                ),
                "workers": [_encode_worker(worker) for worker in heartbeat.workers],
                "protocol_version": heartbeat.protocol_version,
            },
            "service_identity_ref": request.service_identity_ref,
        }

    @staticmethod
    def decode_heartbeat(value: object) -> WorkerHeartbeatRequest:
        data = _mapping(value, "heartbeat request")
        heartbeat_data = _mapping(_required(data, "heartbeat"), "heartbeat")
        resources_raw = heartbeat_data.get("resources")
        node_status_raw = heartbeat_data.get("node_status")
        workers_raw = _array(_required(heartbeat_data, "workers"), "heartbeat.workers")
        return WorkerHeartbeatRequest(
            heartbeat=Heartbeat(
                node_id=_string(_required(heartbeat_data, "node_id"), "heartbeat.node_id"),
                observed_at=_datetime(
                    _required(heartbeat_data, "observed_at"), "heartbeat.observed_at"
                ),
                sequence=_integer(
                    _required(heartbeat_data, "sequence"), "heartbeat.sequence", minimum=1
                ),
                resources=None if resources_raw is None else _decode_resources(resources_raw),
                node_status=(
                    None
                    if node_status_raw is None
                    else NodeStatus(_string(node_status_raw, "heartbeat.node_status"))
                ),
                workers=tuple(_decode_worker(item) for item in workers_raw),
                protocol_version=_string(
                    _required(heartbeat_data, "protocol_version"),
                    "heartbeat.protocol_version",
                ),
            ),
            service_identity_ref=_string(
                _required(data, "service_identity_ref"), "service_identity_ref"
            ),
        )

    @staticmethod
    def encode_receipt(receipt: WorkerProtocolReceipt) -> dict[str, JsonValue]:
        return {
            "node_id": receipt.node_id,
            "reporter_worker_id": receipt.reporter_worker_id,
            "observed_at": receipt.observed_at.astimezone(UTC).isoformat(),
            "worker_ids": list(receipt.worker_ids),
        }

    @staticmethod
    def decode_receipt(value: object) -> WorkerProtocolReceipt:
        data = _mapping(value, "Worker protocol receipt")
        return WorkerProtocolReceipt(
            node_id=_string(_required(data, "node_id"), "node_id"),
            reporter_worker_id=_string(_required(data, "reporter_worker_id"), "reporter_worker_id"),
            observed_at=_datetime(_required(data, "observed_at"), "observed_at"),
            worker_ids=_string_tuple(_required(data, "worker_ids"), "worker_ids"),
        )


class WorkerProtocolHTTP:
    """Framework-neutral private HTTP surface over ``WorkerProtocolService``."""

    def __init__(self, service: WorkerProtocolService) -> None:
        self.service = service

    async def handle(
        self,
        request: WorkerProtocolHTTPRequest,
        *,
        trusted_tls_peer_ref: str | None = None,
    ) -> WorkerProtocolHTTPResponse:
        request_id = _optional_header(request.headers, "x-request-id") or f"worker-http-{uuid4()}"
        correlation_id = _optional_header(request.headers, "x-correlation-id") or request_id
        try:
            if request.method != "POST":
                return self._error(405, "method_not_allowed", "method not allowed", request_id)
            credentials = _request_credentials(
                request.headers,
                request_id=request_id,
                correlation_id=correlation_id,
                trusted_tls_peer_ref=trusted_tls_peer_ref,
            )
            if request.path == f"{WORKER_PROTOCOL_HTTP_PREFIX}/register":
                receipt = await self.service.register(
                    WorkerProtocolCodec.decode_registration(request.body),
                    credentials,
                )
                return self._success(200, WorkerProtocolCodec.encode_receipt(receipt), request_id)
            if request.path == f"{WORKER_PROTOCOL_HTTP_PREFIX}/heartbeat":
                receipt = await self.service.heartbeat(
                    WorkerProtocolCodec.decode_heartbeat(request.body),
                    credentials,
                )
                return self._success(200, WorkerProtocolCodec.encode_receipt(receipt), request_id)
            worker_prefix = f"{WORKER_PROTOCOL_HTTP_PREFIX}/workers/"
            if request.path.startswith(worker_prefix) and request.path.endswith(":deregister"):
                worker_id = request.path[len(worker_prefix) : -len(":deregister")]
                if not worker_id:
                    raise ValueError("worker_id must not be blank")
                node_id = _string(_required(request.body, "node_id"), "node_id")
                await self.service.deregister_worker(worker_id, node_id, credentials)
                return self._success(200, {"deregistered": True}, request_id)
            return self._error(404, "not_found", "Worker protocol route not found", request_id)
        except AuthenticationError:
            return self._error(401, "unauthorized", "Worker authentication failed", request_id)
        except WorkerProtocolAuthorizationError:
            return self._error(
                403, "forbidden", "Worker protocol action is not permitted", request_id
            )
        except (WorkerProtocolError, RegistryError, ValueError) as exc:
            return self._error(400, "invalid_worker_request", str(exc), request_id)

    @staticmethod
    def _success(status: int, body: JsonValue, request_id: str) -> WorkerProtocolHTTPResponse:
        return WorkerProtocolHTTPResponse(
            status=status,
            body=body,
            headers={
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )

    @staticmethod
    def _error(
        status: int,
        code: str,
        message: str,
        request_id: str,
    ) -> WorkerProtocolHTTPResponse:
        return WorkerProtocolHTTPResponse(
            status=status,
            body={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request_id,
                }
            },
            headers={
                "content-type": "application/json",
                "x-request-id": request_id,
            },
        )


ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]
TrustedPeerResolver = Callable[[Mapping[str, Any]], str | None]


class WorkerProtocolASGI:
    """Route only the private Worker protocol prefix before the normal Control Plane app."""

    def __init__(
        self,
        http: WorkerProtocolHTTP,
        *,
        downstream: ASGIApp | None = None,
        trusted_peer_resolver: TrustedPeerResolver | None = None,
        max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        if max_body_bytes < 1024:
            raise ValueError("Worker protocol max_body_bytes must be at least 1024")
        self._http = http
        self._downstream = downstream
        self._trusted_peer_resolver = trusted_peer_resolver
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        path = str(scope.get("path", "/"))
        if scope.get("type") != "http" or not path.startswith(WORKER_PROTOCOL_HTTP_PREFIX):
            if self._downstream is not None:
                await self._downstream(scope, receive, send)
                return
            await _send_asgi_response(
                send,
                WorkerProtocolHTTPResponse(
                    404,
                    {"error": {"code": "not_found", "message": "route not found"}},
                    {"content-type": "application/json"},
                ),
            )
            return

        raw_body = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes):
                raw_body.extend(chunk)
            if len(raw_body) > self._max_body_bytes:
                await _send_asgi_response(
                    send,
                    WorkerProtocolHTTPResponse(
                        413,
                        {"error": {"code": "payload_too_large", "message": "payload too large"}},
                        {"content-type": "application/json"},
                    ),
                )
                return
            if not message.get("more_body", False):
                break

        try:
            decoded: object = {} if not raw_body else json.loads(raw_body.decode("utf-8"))
            body = _mapping(decoded, "request body")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            await _send_asgi_response(
                send,
                WorkerProtocolHTTPResponse(
                    400,
                    {"error": {"code": "invalid_json", "message": "invalid JSON request"}},
                    {"content-type": "application/json"},
                ),
            )
            return

        headers = _decode_asgi_headers(scope.get("headers", []))
        peer_ref = (
            None if self._trusted_peer_resolver is None else self._trusted_peer_resolver(scope)
        )
        response = await self._http.handle(
            WorkerProtocolHTTPRequest(
                method=str(scope.get("method", "GET")),
                path=path,
                headers=headers,
                body=body,
            ),
            trusted_tls_peer_ref=peer_ref,
        )
        await _send_asgi_response(send, response)


class WorkerCredentialProvider(Protocol):
    def __call__(self) -> str: ...


class WorkerProtocolHTTPClient:
    """Dependency-free Worker-side client for the private protocol surface."""

    def __init__(
        self,
        base_url: str,
        *,
        credential_provider: WorkerCredentialProvider,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("Worker protocol base_url must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and not _is_loopback_host(parsed.hostname):
            raise ValueError("non-loopback Worker protocol connections require HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("Worker protocol timeout_seconds must be greater than zero")
        self._base_url = base_url.rstrip("/")
        self._credential_provider = credential_provider
        self._ssl_context = ssl_context
        self._timeout_seconds = timeout_seconds

    async def register(self, request: RegistrationRequest) -> WorkerProtocolReceipt:
        result = await self._post(
            f"{WORKER_PROTOCOL_HTTP_PREFIX}/register",
            WorkerProtocolCodec.encode_registration(request),
        )
        return WorkerProtocolCodec.decode_receipt(result)

    async def heartbeat(self, request: WorkerHeartbeatRequest) -> WorkerProtocolReceipt:
        result = await self._post(
            f"{WORKER_PROTOCOL_HTTP_PREFIX}/heartbeat",
            WorkerProtocolCodec.encode_heartbeat(request),
        )
        return WorkerProtocolCodec.decode_receipt(result)

    async def deregister_worker(self, worker_id: str, node_id: str) -> None:
        if not worker_id.strip() or not node_id.strip():
            raise ValueError("worker_id and node_id must not be blank")
        result = await self._post(
            f"{WORKER_PROTOCOL_HTTP_PREFIX}/workers/{quote(worker_id, safe='')}:deregister",
            {"node_id": node_id},
        )
        data = _mapping(result, "deregister response")
        if data.get("deregistered") is not True:
            raise WorkerProtocolHTTPClientError(
                502,
                "invalid_response",
                "Worker protocol deregistration response is invalid",
            )

    async def _post(self, path: str, body: Mapping[str, JsonValue]) -> object:
        token = self._credential_provider()
        if not token:
            raise ValueError("Worker credential provider returned an empty token")
        request_id = f"worker-http-{uuid4()}"
        nonce = uuid4().hex
        issued_at = datetime.now(UTC).isoformat()
        payload = json.dumps(dict(body), separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "x-worker-nonce": nonce,
            "x-worker-issued-at": issued_at,
            "x-request-id": request_id,
            "x-correlation-id": request_id,
        }

        def operation() -> object:
            request = Request(
                f"{self._base_url}{path}",
                data=payload,
                headers=headers,
                method="POST",
            )
            try:
                with urlopen(
                    request,
                    timeout=self._timeout_seconds,
                    context=self._ssl_context,
                ) as response:
                    raw = response.read()
            except HTTPError as exc:
                raw = exc.read()
                try:
                    error_document: object = json.loads(raw.decode("utf-8"))
                    error_data = _mapping(error_document, "Worker protocol error response")
                    error = _mapping(_required(error_data, "error"), "error")
                    code = _string(_required(error, "code"), "error.code")
                    message = _string(_required(error, "message"), "error.message")
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    code = "http_error"
                    message = "Worker protocol request was rejected"
                raise WorkerProtocolHTTPClientError(
                    exc.code,
                    code,
                    message,
                    retryable=exc.code >= 500,
                ) from None
            except (URLError, TimeoutError, OSError) as exc:
                raise WorkerProtocolHTTPClientError(
                    503,
                    "unavailable",
                    "Worker protocol endpoint is unavailable",
                    retryable=True,
                ) from exc
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkerProtocolHTTPClientError(
                    502,
                    "invalid_response",
                    "Worker protocol response was not valid JSON",
                ) from exc

        return await asyncio.to_thread(operation)


def _encode_node(node: NodeRecord) -> dict[str, JsonValue]:
    return {
        "node_id": node.node_id,
        "display_name": node.display_name,
        "labels": list(node.labels),
        "os_name": node.os_name,
        "platform": node.platform,
        "architecture": node.architecture,
        "supported_runtimes": list(node.supported_runtimes),
        "model_refs": list(node.model_refs),
        "capability_refs": list(node.capability_refs),
        "locality_refs": list(node.locality_refs),
        "resources": _encode_resources(node.resources),
        "adapter_metadata": [_encode_adapter_metadata(item) for item in node.adapter_metadata],
    }


def _decode_node(value: object) -> NodeRecord:
    data = _mapping(value, "Node")
    return NodeRecord(
        node_id=_string(_required(data, "node_id"), "node.node_id"),
        display_name=_string(_required(data, "display_name"), "node.display_name"),
        labels=_string_tuple(data.get("labels", []), "node.labels"),
        os_name=_optional_string(data.get("os_name"), "node.os_name"),
        platform=_optional_string(data.get("platform"), "node.platform"),
        architecture=_optional_string(data.get("architecture"), "node.architecture"),
        supported_runtimes=_string_tuple(
            data.get("supported_runtimes", []), "node.supported_runtimes"
        ),
        model_refs=_string_tuple(data.get("model_refs", []), "node.model_refs"),
        capability_refs=_string_tuple(data.get("capability_refs", []), "node.capability_refs"),
        locality_refs=_string_tuple(data.get("locality_refs", []), "node.locality_refs"),
        resources=_decode_resources(_required(data, "resources")),
        adapter_metadata=_decode_adapter_metadata_tuple(
            data.get("adapter_metadata", []), "node.adapter_metadata"
        ),
    )


def _encode_worker(worker: WorkerRecord) -> dict[str, JsonValue]:
    return {
        "worker_id": worker.worker_id,
        "node_id": worker.node_id,
        "worker_type": worker.worker_type,
        "supported_executors": list(worker.supported_executors),
        "capability_refs": list(worker.capability_refs),
        "supported_runtimes": list(worker.supported_runtimes),
        "model_refs": list(worker.model_refs),
        "concurrency_limit": worker.concurrency_limit,
        "active_jobs": worker.active_jobs,
        "status": worker.status.value,
        "protocol_version": worker.protocol_version,
        "worker_version": worker.worker_version,
        "locality_refs": list(worker.locality_refs),
        "adapter_metadata": [_encode_adapter_metadata(item) for item in worker.adapter_metadata],
    }


def _decode_worker(value: object) -> WorkerRecord:
    data = _mapping(value, "Worker")
    return WorkerRecord(
        worker_id=_string(_required(data, "worker_id"), "worker.worker_id"),
        node_id=_string(_required(data, "node_id"), "worker.node_id"),
        worker_type=_string(data.get("worker_type", "execution"), "worker.worker_type"),
        supported_executors=_string_tuple(
            data.get("supported_executors", []), "worker.supported_executors"
        ),
        capability_refs=_string_tuple(data.get("capability_refs", []), "worker.capability_refs"),
        supported_runtimes=_string_tuple(
            data.get("supported_runtimes", []), "worker.supported_runtimes"
        ),
        model_refs=_string_tuple(data.get("model_refs", []), "worker.model_refs"),
        concurrency_limit=_integer(
            data.get("concurrency_limit", 1), "worker.concurrency_limit", minimum=1
        ),
        active_jobs=_integer(data.get("active_jobs", 0), "worker.active_jobs", minimum=0),
        status=WorkerStatus(_string(data.get("status", "healthy"), "worker.status")),
        protocol_version=_string(
            data.get("protocol_version", WORKER_PROTOCOL_VERSION), "worker.protocol_version"
        ),
        worker_version=_string(data.get("worker_version", "0"), "worker.worker_version"),
        locality_refs=_string_tuple(data.get("locality_refs", []), "worker.locality_refs"),
        adapter_metadata=_decode_adapter_metadata_tuple(
            data.get("adapter_metadata", []), "worker.adapter_metadata"
        ),
    )


def _encode_resources(resources: ResourceSnapshot) -> dict[str, JsonValue]:
    return {
        "cpu_cores_total": resources.cpu_cores_total,
        "cpu_cores_available": resources.cpu_cores_available,
        "ram_total_bytes": resources.ram_total_bytes,
        "ram_available_bytes": resources.ram_available_bytes,
        "storage_total_bytes": resources.storage_total_bytes,
        "storage_available_bytes": resources.storage_available_bytes,
        "accelerators": [_encode_accelerator(item) for item in resources.accelerators],
    }


def _decode_resources(value: object) -> ResourceSnapshot:
    data = _mapping(value, "resources")
    return ResourceSnapshot(
        cpu_cores_total=_number(data.get("cpu_cores_total", 0), "resources.cpu_cores_total"),
        cpu_cores_available=_number(
            data.get("cpu_cores_available", 0), "resources.cpu_cores_available"
        ),
        ram_total_bytes=_integer(
            data.get("ram_total_bytes", 0), "resources.ram_total_bytes", minimum=0
        ),
        ram_available_bytes=_integer(
            data.get("ram_available_bytes", 0), "resources.ram_available_bytes", minimum=0
        ),
        storage_total_bytes=_integer(
            data.get("storage_total_bytes", 0), "resources.storage_total_bytes", minimum=0
        ),
        storage_available_bytes=_integer(
            data.get("storage_available_bytes", 0),
            "resources.storage_available_bytes",
            minimum=0,
        ),
        accelerators=tuple(
            _decode_accelerator(item)
            for item in _array(data.get("accelerators", []), "resources.accelerators")
        ),
    )


def _encode_accelerator(accelerator: AcceleratorResource) -> dict[str, JsonValue]:
    return {
        "accelerator_id": accelerator.accelerator_id,
        "kind": accelerator.kind,
        "vendor": accelerator.vendor,
        "model": accelerator.model,
        "memory_total_bytes": accelerator.memory_total_bytes,
        "memory_available_bytes": accelerator.memory_available_bytes,
    }


def _decode_accelerator(value: object) -> AcceleratorResource:
    data = _mapping(value, "accelerator")
    return AcceleratorResource(
        accelerator_id=_string(_required(data, "accelerator_id"), "accelerator.accelerator_id"),
        kind=_string(data.get("kind", "gpu"), "accelerator.kind"),
        vendor=_optional_string(data.get("vendor"), "accelerator.vendor"),
        model=_optional_string(data.get("model"), "accelerator.model"),
        memory_total_bytes=_integer(
            data.get("memory_total_bytes", 0), "accelerator.memory_total_bytes", minimum=0
        ),
        memory_available_bytes=_integer(
            data.get("memory_available_bytes", 0),
            "accelerator.memory_available_bytes",
            minimum=0,
        ),
    )


def _encode_adapter_metadata(metadata: AdapterMetadata) -> dict[str, JsonValue]:
    return {"namespace": metadata.namespace, "values": dict(metadata.values)}


def _decode_adapter_metadata_tuple(value: object, label: str) -> tuple[AdapterMetadata, ...]:
    return tuple(_decode_adapter_metadata(item) for item in _array(value, label))


def _decode_adapter_metadata(value: object) -> AdapterMetadata:
    data = _mapping(value, "adapter metadata")
    values = _mapping(data.get("values", {}), "adapter metadata values")
    return AdapterMetadata(
        namespace=_string(_required(data, "namespace"), "adapter metadata namespace"),
        values={key: cast(JsonValue, item) for key, item in values.items()},
    )


def _request_credentials(
    headers: Mapping[str, str],
    *,
    request_id: str,
    correlation_id: str,
    trusted_tls_peer_ref: str | None,
) -> WorkerRequestCredentials:
    try:
        authorization = _required_header(headers, "authorization")
        nonce = _required_header(headers, "x-worker-nonce")
        issued_at = _datetime(
            _required_header(headers, "x-worker-issued-at"),
            "x-worker-issued-at",
        )
    except ValueError as exc:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS) from exc
    prefix = "Bearer "
    if not authorization.startswith(prefix) or not authorization[len(prefix) :]:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
    try:
        return WorkerRequestCredentials(
            token=authorization[len(prefix) :],
            nonce=nonce,
            issued_at=issued_at,
            tls_peer_ref=trusted_tls_peer_ref,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS) from exc


def _decode_asgi_headers(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    headers: dict[str, str] = {}
    for item in value:
        if not isinstance(item, tuple | list) or len(item) != 2:
            continue
        raw_name, raw_value = item
        if isinstance(raw_name, bytes) and isinstance(raw_value, bytes):
            headers[raw_name.decode("latin-1").lower()] = raw_value.decode("latin-1")
    return headers


async def _send_asgi_response(send: ASGISend, response: WorkerProtocolHTTPResponse) -> None:
    payload = json.dumps(response.body, separators=(",", ":")).encode("utf-8")
    headers = [
        (key.encode("latin-1"), value.encode("latin-1")) for key, value in response.headers.items()
    ]
    headers.append((b"content-length", str(len(payload)).encode("ascii")))
    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = _optional_header(headers, name)
    if value is None or not value.strip():
        raise ValueError(f"required Worker protocol header is missing: {name}")
    return value


def _optional_header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if key.casefold() == target:
            return value
    return None


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{label} keys must be strings")
        result[key] = item
    return result


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _required(data: Mapping[str, object], name: str) -> object:
    if name not in data:
        raise ValueError(f"required field is missing: {name}")
    return data[name]


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{label}[]") for item in _array(value, label))


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _datetime(value: object, label: str) -> datetime:
    raw = _string(value, label)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 date-time") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
