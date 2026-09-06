from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import shutil
import ssl
import subprocess
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.adapters.distributed_control_plane_app import (
    build_distributed_control_plane_deployment,
)
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.distributed_worker import main as worker_main
from ai_multi_agent_platform.distributed import RegistryError, WorkerStatus
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.messaging import TcpMessageBroker

_REMOTE_PROFILE = Path("deploy/distributed/profiles/remote-worker.json")
_PASSWORD = "issue-240-secure-entrypoint-password"


def _run_openssl(openssl: str, *args: str) -> None:
    subprocess.run(
        [openssl, *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _generate_mtls_material(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise AssertionError("OpenSSL CLI is required for the secure #240 acceptance test")

    ca_key = tmp_path / "ca.key"
    ca_cert = tmp_path / "ca.crt"
    server_key = tmp_path / "server.key"
    server_csr = tmp_path / "server.csr"
    server_cert = tmp_path / "server.crt"
    server_ext = tmp_path / "server.ext"
    client_key = tmp_path / "client.key"
    client_csr = tmp_path / "client.csr"
    client_cert = tmp_path / "client.crt"
    client_ext = tmp_path / "client.ext"

    _run_openssl(
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=AI Multi Agent Issue 240 CA",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_cert),
    )
    _run_openssl(
        openssl,
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=localhost",
        "-keyout",
        str(server_key),
        "-out",
        str(server_csr),
    )
    server_ext.write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth\n"
        "keyUsage=digitalSignature,keyEncipherment\n",
        encoding="utf-8",
    )
    _run_openssl(
        openssl,
        "x509",
        "-req",
        "-in",
        str(server_csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-days",
        "1",
        "-sha256",
        "-extfile",
        str(server_ext),
        "-out",
        str(server_cert),
    )
    _run_openssl(
        openssl,
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=issue-240-worker-client",
        "-keyout",
        str(client_key),
        "-out",
        str(client_csr),
    )
    client_ext.write_text(
        "extendedKeyUsage=clientAuth\nkeyUsage=digitalSignature,keyEncipherment\n",
        encoding="utf-8",
    )
    _run_openssl(
        openssl,
        "x509",
        "-req",
        "-in",
        str(client_csr),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-days",
        "1",
        "-sha256",
        "-extfile",
        str(client_ext),
        "-out",
        str(client_cert),
    )
    return ca_cert, server_cert, server_key, client_cert, client_key


def _server_ssl_context(
    ca_cert: Path,
    server_cert: Path,
    server_key: Path,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(server_cert, server_key)
    context.load_verify_locations(cafile=ca_cert)
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _worker_entrypoint(argv: list[str], environment: dict[str, str]) -> None:
    os.environ.update(environment)
    raise SystemExit(worker_main(argv))


async def _serve_asgi_request(
    app: Any,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    observed_peer_certificates: list[dict[str, Any]],
) -> None:
    try:
        raw_headers = await reader.readuntil(b"\r\n\r\n")
        header_lines = raw_headers[:-4].decode("iso-8859-1").split("\r\n")
        method, target, _http_version = header_lines[0].split(" ", 2)
        path, _, query = target.partition("?")
        headers: list[tuple[bytes, bytes]] = []
        header_map: dict[str, str] = {}
        for line in header_lines[1:]:
            name, separator, value = line.partition(":")
            if not separator:
                continue
            normalized_name = name.strip().lower()
            normalized_value = value.strip()
            header_map[normalized_name] = normalized_value
            headers.append(
                (
                    normalized_name.encode("ascii"),
                    normalized_value.encode("iso-8859-1"),
                )
            )
        content_length = int(header_map.get("content-length", "0"))
        body = await reader.readexactly(content_length) if content_length else b""

        ssl_object = writer.get_extra_info("ssl_object")
        if not isinstance(ssl_object, ssl.SSLObject):
            raise AssertionError("secure Worker protocol test accepted a non-TLS connection")
        peer_certificate = ssl_object.getpeercert()
        if not peer_certificate:
            raise AssertionError("secure Worker protocol test accepted a client without mTLS")
        observed_peer_certificates.append(peer_certificate)

        receive_used = False

        async def receive() -> dict[str, Any]:
            nonlocal receive_used
            if receive_used:
                return {"type": "http.disconnect"}
            receive_used = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        response_status = 500
        response_headers: list[tuple[bytes, bytes]] = []
        response_body = bytearray()

        async def send(message: dict[str, Any]) -> None:
            nonlocal response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
                response_headers = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if isinstance(chunk, bytes):
                    response_body.extend(chunk)

        peername = writer.get_extra_info("peername")
        sockname = writer.get_extra_info("sockname")
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.5"},
                "http_version": "1.1",
                "method": method,
                "scheme": "https",
                "path": path,
                "raw_path": path.encode("utf-8"),
                "query_string": query.encode("ascii"),
                "headers": headers,
                "client": peername,
                "server": sockname,
            },
            receive,
            send,
        )

        reason = HTTPStatus(response_status).phrase
        names = {name.lower() for name, _value in response_headers}
        writer.write(f"HTTP/1.1 {response_status} {reason}\r\n".encode("ascii"))
        for name, value in response_headers:
            writer.write(name + b": " + value + b"\r\n")
        if b"content-length" not in names:
            writer.write(f"Content-Length: {len(response_body)}\r\n".encode("ascii"))
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(response_body)
        await writer.drain()
    finally:
        writer.close()
        with suppress(ConnectionError, OSError, ssl.SSLError):
            await writer.wait_closed()


async def _wait_for_worker(
    deployment: Any,
    worker_id: str,
    process: multiprocessing.Process,
) -> None:
    runtime = deployment.distributed_runtime
    assert runtime is not None
    for _ in range(200):
        if process.exitcode is not None:
            raise AssertionError(
                f"platform-worker exited before registration with code {process.exitcode}"
            )
        try:
            worker = runtime.registry.get_worker(worker_id)
        except RegistryError:
            pass
        else:
            if worker.status is WorkerStatus.HEALTHY:
                return
        await asyncio.sleep(0.05)
    raise AssertionError("platform-worker did not become healthy through the secure entrypoint")


def test_secure_profile_provisioning_worker_entrypoint_and_task_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        ca_cert, server_cert, server_key, client_cert, client_key = _generate_mtls_material(
            tmp_path
        )
        server_context = _server_ssl_context(ca_cert, server_cert, server_key)
        broker = TcpMessageBroker(host="0.0.0.0", ssl_context=server_context)
        await broker.start()

        monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_HOST", "127.0.0.1")
        monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_PORT", str(broker.port))
        monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_CA_FILE", str(ca_cert))
        monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_CLIENT_CERT", str(client_cert))
        monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_CLIENT_KEY", str(client_key))
        monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_SERVER_HOSTNAME", "localhost")
        monkeypatch.delenv("PLATFORM_TRANSPORT_AUTH_KEY", raising=False)

        raw_profile = json.loads(_REMOTE_PROFILE.read_text(encoding="utf-8"))
        raw_profile["nodes"][0]["deployment"]["workspace_root"] = str(
            (tmp_path / "remote-worker-workspaces").resolve()
        )
        profile_path = tmp_path / "remote-worker-secure.json"
        profile_path.write_text(json.dumps(raw_profile), encoding="utf-8")
        node = raw_profile["nodes"][0]
        worker_id = str(node["workers"][0]["worker_id"])
        host_ref = str(node["deployment"]["host_ref"])

        deployment = await asyncio.to_thread(
            build_distributed_control_plane_deployment,
            SingleNodeConfig(data_dir=tmp_path / "control-plane", secure_cookie=False),
            profile_path=str(profile_path),
        )
        runtime = deployment.distributed_runtime
        assert runtime is not None

        admin = deployment.bootstrap_admin("issue240-secure-admin", _PASSWORD)
        admin_token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-240-secure-entrypoint",
        )
        admin_headers = {
            "authorization": f"Bearer {admin_token.secret}",
            "content-type": "application/json",
        }
        provisioned = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/worker.provision",
                headers={
                    **admin_headers,
                    "idempotency-key": "issue-240-secure-worker-provision",
                },
                body={"resource_ref": worker_id},
            )
        )
        assert provisioned.status == 200, provisioned.body
        assert isinstance(provisioned.body, dict)
        worker_secret = provisioned.body.get("secret")
        assert isinstance(worker_secret, str)
        assert provisioned.body.get("secret_display") == "one_time"

        observed_peer_certificates: list[dict[str, Any]] = []
        server = await asyncio.start_server(
            lambda reader, writer: _serve_asgi_request(
                deployment.app,
                reader,
                writer,
                observed_peer_certificates,
            ),
            "127.0.0.1",
            0,
            ssl=server_context,
        )
        assert server.sockets
        control_plane_port = int(server.sockets[0].getsockname()[1])

        ctx = multiprocessing.get_context("spawn")
        process = ctx.Process(
            target=_worker_entrypoint,
            args=(
                [
                    "--profile",
                    str(profile_path),
                    "--host-ref",
                    host_ref,
                    "--worker-id",
                    worker_id,
                    "--control-plane-url",
                    f"https://localhost:{control_plane_port}",
                    "--broker-host",
                    "127.0.0.1",
                    "--broker-port",
                    str(broker.port),
                    "--ca-file",
                    str(ca_cert),
                    "--client-cert",
                    str(client_cert),
                    "--client-key",
                    str(client_key),
                    "--server-hostname",
                    "localhost",
                    "--heartbeat-seconds",
                    "0.1",
                ],
                {
                    "PLATFORM_WORKER_TOKEN": worker_secret,
                    "PLATFORM_TRANSPORT_AUTH_KEY": "",
                },
            ),
        )
        process.start()

        try:
            await _wait_for_worker(deployment, worker_id, process)
            assert observed_peer_certificates

            created = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/tasks",
                    headers={
                        **admin_headers,
                        "idempotency-key": "issue-240-secure-task-create",
                    },
                    body={
                        "title": "Secure distributed Task",
                        "objective": "Execute through the shipped secure Worker entrypoint",
                        "owner_type": "user",
                        "owner_id": admin.user_id,
                    },
                )
            )
            assert created.status == 201, created.body
            assert isinstance(created.body, dict)
            task_id = created.body.get("id")
            assert isinstance(task_id, str)

            queued = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path=f"/api/v1/tasks/{task_id}:queue",
                    headers={
                        **admin_headers,
                        "idempotency-key": "issue-240-secure-task-queue",
                    },
                )
            )
            assert queued.status == 200, queued.body

            started = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path=f"/api/v1/tasks/{task_id}:start",
                    headers={
                        **admin_headers,
                        "idempotency-key": "issue-240-secure-task-start",
                    },
                )
            )
            assert started.status == 200, started.body
            assert isinstance(started.body, dict)
            run_id = started.body.get("id")
            assert isinstance(run_id, str)

            refreshed = await deployment.kernel.refresh_run(
                idempotency_key="issue-240-secure-task-refresh",
                task_id=task_id,
                run_id=run_id,
            )
            task = await deployment.kernel.get_task(task_id)
            assert refreshed.status is RunStatus.SUCCEEDED
            assert task.status is TaskStatus.SUCCEEDED

            matching_records = [
                record for record in runtime.records() if record.job.execution.run_id == run_id
            ]
            assert len(matching_records) == 1
            assert matching_records[0].worker_id == worker_id
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            server.close()
            await server.wait_closed()
            await broker.close(graceful=False)

    asyncio.run(scenario())