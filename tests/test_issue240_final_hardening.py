from __future__ import annotations

import asyncio
import hashlib
import shutil
import ssl
import subprocess
from contextlib import suppress
from pathlib import Path

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import WorkerJobRequest
from ai_multi_agent_platform.distributed.workspace import WorkspaceJobMaterializationResolver
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.messaging import (
    InProcessMessageTransport,
    MessageKind,
    Subscription,
    TcpMessageBroker,
    TcpMessageTransport,
    TransportEnvelope,
)
from ai_multi_agent_platform.workspaces import (
    WorkspaceAccessMode,
    WorkspaceChangeKind,
    WorkspaceFile,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-240-final-hardening",
            owner_type="service",
            owner_id="issue-240-final-hardening",
            project_id=project_id,
        ),
        actor_ref="service:issue-240-final-hardening",
    )


def _execution_request(project_id: str) -> ExecutionRequest:
    task_id = new_id("task")
    return ExecutionRequest(
        run_id=new_id("run"),
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(
            correlation_id=f"issue-240-final-hardening:{task_id}",
            project_id=project_id,
        ),
    )


async def _empty_workspace(tmp_path: Path):
    project_id = new_id("project")
    context = _context(project_id)
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
    record = await files.create_file(b"", context, content_type="application/octet-stream")
    workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
    workspace = await workspaces.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="service", id="issue-240-final-hardening"),
        workspace_type=WorkspaceType.REMOTE,
        context=context,
        access_mode=WorkspaceAccessMode.READ_WRITE,
        files=(
            WorkspaceFile(
                relative_path="src/empty-input.bin",
                file_id=record.file_id,
                sha256=record.sha256,
            ),
        ),
    )
    assert workspace.base_snapshot_id is not None
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)
    request = await WorkspaceJobMaterializationResolver(workspaces).resolve(
        WorkerJobRequest(
            execution=_execution_request(project_id),
            workspace_ref=workspace.id,
            snapshot_ref=snapshot.id,
        )
    )
    assert request is not None
    return workspaces, files, context, workspace, snapshot, request


async def _read_file(
    files: LocalFileProvider,
    file_id: str,
    context: DataAccessContext,
) -> bytes:
    return b"".join([chunk async for chunk in files.stream_file(file_id, context)])


def test_zero_byte_workspace_input_and_result_survive_remote_transfer(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _empty_workspace(tmp_path)
        worker_id = new_id("worker")
        worker_root = tmp_path / "worker-root"
        transport = InProcessMessageTransport(provider_id="issue-240-zero-byte")
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, transport).serve()
        )
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,
            workspaces,
            files,
            lambda _workspace: context,
        )
        try:
            receipt = await materializer.materialize(request)
            materialized_input = (
                worker_root / workspace.id / snapshot.id / "src" / "empty-input.bin"
            )
            assert materialized_input.is_file()
            assert materialized_input.read_bytes() == b""
            assert materialized_input.stat().st_size == 0

            empty_result = worker_root / workspace.id / snapshot.id / "out" / "empty-result.bin"
            empty_result.parent.mkdir(parents=True, exist_ok=True)
            empty_result.write_bytes(b"")

            result = await materializer.collect_result(receipt)
            created = [
                change
                for change in result.changes
                if change.relative_path == "out/empty-result.bin"
            ]
            assert len(created) == 1
            change = created[0]
            assert change.kind is WorkspaceChangeKind.CREATED
            assert change.sha256 == hashlib.sha256(b"").hexdigest()
            assert change.file_id is not None
            assert await _read_file(files, change.file_id, context) == b""
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


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
        raise AssertionError("OpenSSL CLI is required for the positive mTLS acceptance test")

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
        "/CN=AI Multi Agent Test CA",
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
        "/CN=issue-240-test-client",
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


def test_tcp_transport_succeeds_with_verified_mtls_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        ca_cert, server_cert, server_key, client_cert, client_key = _generate_mtls_material(
            tmp_path
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(server_cert, server_key)
        server_context.load_verify_locations(cafile=ca_cert)
        server_context.verify_mode = ssl.CERT_REQUIRED

        client_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_cert)
        client_context.load_cert_chain(client_cert, client_key)

        broker = TcpMessageBroker(host="0.0.0.0", ssl_context=server_context)
        await broker.start()
        transport = TcpMessageTransport(
            "127.0.0.1",
            broker.port,
            ssl_context=client_context,
            server_hostname="localhost",
            provider_id="issue-240-positive-mtls",
        )
        stream = transport.subscribe(
            Subscription("issue240.mtls", "issue240-consumer", "issue240-group")
        )
        envelope = TransportEnvelope(
            message_type="issue240.mtls.acceptance",
            kind=MessageKind.COMMAND,
            payload_schema_version="1",
            source_component="issue240-final-hardening",
            correlation_id="issue240-mtls",
            idempotency_key="issue240-mtls-idempotency",
            payload={"secure": True},
        )
        try:
            assert await transport.check_ready() is True
            receipt = await transport.publish("issue240.mtls", envelope)
            delivery = await asyncio.wait_for(anext(stream), timeout=3.0)
            assert receipt.message_id == envelope.message_id
            assert delivery.envelope == envelope
            await transport.ack(delivery)
        finally:
            await stream.aclose()
            await transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
