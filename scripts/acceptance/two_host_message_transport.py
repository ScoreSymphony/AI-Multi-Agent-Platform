"""Operator-run acceptance harness for the real two-host #388 message transport path.

The harness consumes the existing #35 TCP MessageTransport adapter and canonical
#14 Worker command/reply path. It does not introduce another transport or Worker
identity model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import ssl
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ai_multi_agent_platform.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.distributed import (
    LocalWorker,
    TransportWorkerDispatcher,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.messaging import TcpMessageTransport

REPORT_SCHEMA = "ai-multi-agent-platform/issue-388-two-host-transport/v1"
RESTART_REPORT_SCHEMA = "ai-multi-agent-platform/issue-388-two-host-restart/v1"


class _AcceptanceLifecycle(LifecycleBackend):
    """Deterministic no-paid-service lifecycle used only by the acceptance Worker."""

    def __init__(self, *, worker_instance_ref: str, worker_host_label: str) -> None:
        self._worker_instance_ref = worker_instance_ref
        self._worker_host_label = worker_host_label
        self._states: dict[str, RunStatus] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="issue388-two-host-acceptance",
            provider_type="lifecycle",
        )

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self._states.setdefault(request.run_id, RunStatus.SUCCEEDED)
        return ExecutionHandle(
            run_id=request.run_id,
            backend_ref="issue388-two-host-acceptance",
        )

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        try:
            status = self._states[run_id]
        except KeyError as exc:
            raise RuntimeError(f"acceptance run is unknown: {run_id}") from exc
        return ExecutionSnapshot(
            run_id=run_id,
            status=status,
            output={
                "acceptance": "issue388-two-host-message-transport",
                "worker_instance_ref": self._worker_instance_ref,
                "worker_host_label": self._worker_host_label,
            },
        )

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        self._states[run_id] = RunStatus.CANCELLED
        return await self.get(run_id, context)


class _AcceptanceWorker(LocalWorker):
    """Worker that contributes explicit Artifact and Evidence references to its result."""

    def __init__(
        self,
        worker_id: str,
        lifecycle: LifecycleBackend,
        *,
        output_artifact_ref: str,
        evidence_ref: str,
        worker_instance_ref: str,
    ) -> None:
        super().__init__(worker_id, lifecycle)
        self._output_artifact_ref = output_artifact_ref
        self._evidence_ref = evidence_ref
        self._worker_instance_ref = worker_instance_ref

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        result = await super().result(worker_job_id)
        if result is None:
            return None
        artifact_refs = tuple(dict.fromkeys((*result.artifact_refs, self._output_artifact_ref)))
        return replace(
            result,
            artifact_refs=artifact_refs,
            evidence_refs=(self._evidence_ref, self._worker_instance_ref),
        )


def _add_transport_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--broker-host", required=True)
    parser.add_argument("--broker-port", required=True, type=int)
    parser.add_argument(
        "--ca-file",
        required=True,
        type=Path,
        help="CA certificate used to verify the broker TLS certificate.",
    )
    parser.add_argument(
        "--server-hostname",
        required=True,
        help="Expected TLS server identity; use the certificate SAN/CN, not a secret.",
    )
    parser.add_argument("--cert-file", type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument(
        "--transport-auth-env",
        default="PLATFORM_TRANSPORT_AUTH_KEY",
        help="Environment variable containing an optional HMAC key. Never pass the key on argv.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real two-host #388 MessageTransport acceptance path."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    worker = subparsers.add_parser(
        "worker",
        help="Run the canonical Worker transport endpoint on Host B.",
    )
    _add_transport_arguments(worker)
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--worker-host-label", required=True)
    worker.add_argument("--output-artifact-ref", required=True)
    worker.add_argument(
        "--evidence-ref",
        default="evidence:issue388-two-host",
    )

    control = subparsers.add_parser(
        "control",
        help="Dispatch and retrieve a canonical Worker job from Host A.",
    )
    _add_transport_arguments(control)
    control.add_argument("--worker-id", required=True)
    control.add_argument("--control-host-label", required=True)
    control.add_argument("--expected-worker-host-label", required=True)
    control.add_argument("--output-artifact-ref", required=True)
    control.add_argument(
        "--evidence-ref",
        default="evidence:issue388-two-host",
    )
    control.add_argument("--timeout-seconds", type=float, default=15.0)
    control.add_argument("--json-report", required=True, type=Path)

    verify = subparsers.add_parser(
        "verify-restart",
        help="Verify two passing runs used the same Worker identity across a process restart.",
    )
    verify.add_argument("first_report", type=Path)
    verify.add_argument("second_report", type=Path)
    verify.add_argument("--json-report", required=True, type=Path)
    return parser


def _client_ssl_context(args: argparse.Namespace) -> ssl.SSLContext:
    cert_file = args.cert_file
    key_file = args.key_file
    if (cert_file is None) != (key_file is None):
        raise ValueError("--cert-file and --key-file must be supplied together")
    context = ssl.create_default_context(
        ssl.Purpose.SERVER_AUTH,
        cafile=str(args.ca_file),
    )
    if cert_file is not None and key_file is not None:
        context.load_cert_chain(str(cert_file), str(key_file))
    return context


def _authentication_key(args: argparse.Namespace) -> str | None:
    value = os.environ.get(str(args.transport_auth_env))
    return value or None


def _authentication_mode(args: argparse.Namespace, authentication_key: str | None) -> str:
    has_mtls = args.cert_file is not None and args.key_file is not None
    if not has_mtls and authentication_key is None:
        raise ValueError(
            "two-host acceptance requires a client certificate (mTLS) or a runtime HMAC key"
        )
    if has_mtls and authentication_key is not None:
        return "mtls+hmac"
    if has_mtls:
        return "mtls"
    return "tls+hmac"


def _transport(
    args: argparse.Namespace,
    *,
    provider_id: str,
) -> tuple[TcpMessageTransport, str]:
    authentication_key = _authentication_key(args)
    authentication = _authentication_mode(args, authentication_key)
    return (
        TcpMessageTransport(
            str(args.broker_host),
            int(args.broker_port),
            ssl_context=_client_ssl_context(args),
            server_hostname=str(args.server_hostname),
            authentication_key=authentication_key,
            provider_id=provider_id,
        ),
        authentication,
    )


async def _run_worker(args: argparse.Namespace) -> None:
    worker_instance_ref = f"evidence:transport-worker-instance:{uuid4().hex}"
    lifecycle = _AcceptanceLifecycle(
        worker_instance_ref=worker_instance_ref,
        worker_host_label=str(args.worker_host_label),
    )
    worker = _AcceptanceWorker(
        str(args.worker_id),
        lifecycle,
        output_artifact_ref=str(args.output_artifact_ref),
        evidence_ref=str(args.evidence_ref),
        worker_instance_ref=worker_instance_ref,
    )
    transport, authentication = _transport(
        args,
        provider_id="issue388-two-host-worker",
    )
    if not await transport.check_ready():
        await transport.close(graceful=False)
        raise RuntimeError("message broker readiness probe failed")

    endpoint = WorkerTransportEndpoint(worker, transport)
    endpoint_task = asyncio.create_task(endpoint.serve())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signum, stop.set)

    print(
        json.dumps(
            {
                "status": "ready",
                "worker_id": worker.worker_id,
                "worker_instance_ref": worker_instance_ref,
                "worker_host_label": str(args.worker_host_label),
                "transport": "TcpMessageTransport",
                "tls": True,
                "authentication": authentication,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {endpoint_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if endpoint_task in done:
            error = endpoint_task.exception()
            if error is not None:
                raise error
            raise RuntimeError("Worker transport endpoint stopped unexpectedly")
    finally:
        stop_task.cancel()
        endpoint_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        with suppress(asyncio.CancelledError):
            await endpoint_task
        await transport.close(graceful=True)


def _operation_context(project_id: str, worker_job_id: str) -> OperationContext:
    return OperationContext(
        correlation_id=f"issue388-two-host:{worker_job_id}",
        owner_type="service",
        owner_id="service:issue388-two-host-acceptance",
        project_id=project_id,
    )


async def _await_result(
    dispatcher: TransportWorkerDispatcher,
    worker_job_id: str,
    *,
    timeout_seconds: float,
) -> WorkerJobResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        result = await dispatcher.result(worker_job_id)
        if result is not None:
            return result
        if loop.time() >= deadline:
            raise RuntimeError(f"Worker result timed out for {worker_job_id}")
        await asyncio.sleep(0.1)


def _string_output(output: Mapping[str, object], key: str) -> str:
    value = output.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Worker result output is missing {key}")
    return value


async def _run_control(args: argparse.Namespace) -> dict[str, object]:
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be greater than zero")
    if args.control_host_label == args.expected_worker_host_label:
        raise ValueError("control and Worker host labels must be distinct for two-host evidence")

    transport, authentication = _transport(
        args,
        provider_id="issue388-two-host-control",
    )
    try:
        if not await transport.check_ready():
            raise RuntimeError("message broker readiness probe failed")

        worker_id = str(args.worker_id)
        worker_job_id = new_id("worker_job")
        project_id = new_id("project")
        task_id = new_id("task")
        run_id = new_id("run")
        input_artifact_ref = new_id("artifact")
        context = _operation_context(project_id, worker_job_id)
        job = WorkerJobRequest(
            worker_job_id=worker_job_id,
            execution=ExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=context,
                input={"acceptance": "issue388-two-host-message-transport"},
            ),
            artifact_refs=(input_artifact_ref,),
            timeout_seconds=float(args.timeout_seconds),
            idempotency_key=f"{worker_job_id}:issue388-two-host",
        )
        dispatcher = TransportWorkerDispatcher(
            worker_id,
            transport,
            client_id="issue388-two-host-control",
            response_timeout_seconds=float(args.timeout_seconds),
        )

        first_handle = await dispatcher.dispatch(job)
        repeated_handle = await dispatcher.dispatch(job)
        if first_handle != repeated_handle or first_handle.run_id != run_id:
            raise RuntimeError("repeated dispatch did not preserve the canonical Worker Job/Run")

        result = await _await_result(
            dispatcher,
            worker_job_id,
            timeout_seconds=float(args.timeout_seconds),
        )
        if result.worker_id != worker_id:
            raise RuntimeError("Worker result identity does not match the requested Worker")
        if result.status.value != "succeeded":
            raise RuntimeError(f"Worker result is not successful: {result.status.value}")
        if input_artifact_ref not in result.artifact_refs:
            raise RuntimeError("input Artifact reference did not survive result retrieval")
        output_artifact_ref = str(args.output_artifact_ref)
        if output_artifact_ref not in result.artifact_refs:
            raise RuntimeError(
                "Worker-produced Artifact reference did not survive result retrieval"
            )
        evidence_ref = str(args.evidence_ref)
        if evidence_ref not in result.evidence_refs:
            raise RuntimeError("Worker Evidence reference did not survive result retrieval")
        if result.execution is None:
            raise RuntimeError("Worker result is missing the canonical execution snapshot")

        worker_instance_ref = _string_output(
            result.execution.output,
            "worker_instance_ref",
        )
        worker_host_label = _string_output(
            result.execution.output,
            "worker_host_label",
        )
        if worker_instance_ref not in result.evidence_refs:
            raise RuntimeError(
                "Worker instance Evidence reference did not survive result retrieval"
            )
        if worker_host_label != str(args.expected_worker_host_label):
            raise RuntimeError(
                "Worker-reported host label does not match the expected Host B label"
            )

        return {
            "schema": REPORT_SCHEMA,
            "status": "pass",
            "observed_at": datetime.now(UTC).isoformat(),
            "control_host_label": str(args.control_host_label),
            "worker_host_label": worker_host_label,
            "transport": {
                "provider": "TcpMessageTransport",
                "tls": True,
                "authentication": authentication,
            },
            "worker_id": worker_id,
            "worker_instance_ref": worker_instance_ref,
            "worker_job_id": worker_job_id,
            "run_id": run_id,
            "task_id": task_id,
            "project_id": project_id,
            "correlation_id": context.correlation_id,
            "artifact_refs": list(result.artifact_refs),
            "evidence_refs": list(result.evidence_refs),
            "expected_input_artifact_ref": input_artifact_ref,
            "expected_output_artifact_ref": output_artifact_ref,
            "expected_evidence_ref": evidence_ref,
            "repeat_dispatch_same_handle": True,
            "broker_address_recorded": False,
            "credential_material_recorded": False,
        }
    finally:
        await transport.close(graceful=True)


def _load_report(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"acceptance report must be a JSON object: {path}")
    return data


def _required_report_string(report: Mapping[str, object], key: str) -> str:
    value = report.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"acceptance report is missing {key}")
    return value


def _report_has_secure_transport(report: Mapping[str, object]) -> bool:
    transport = report.get("transport")
    if not isinstance(transport, dict):
        return False
    return transport.get("tls") is True and transport.get("authentication") in {
        "mtls",
        "tls+hmac",
        "mtls+hmac",
    }


def _verify_restart(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> dict[str, object]:
    for report in (first, second):
        if report.get("schema") != REPORT_SCHEMA or report.get("status") != "pass":
            raise ValueError("both inputs must be passing #388 two-host transport reports")
        if not _report_has_secure_transport(report):
            raise ValueError("both inputs must prove authenticated encrypted transport")
        if report.get("repeat_dispatch_same_handle") is not True:
            raise ValueError("both inputs must prove repeated dispatch idempotency")

    first_worker_id = _required_report_string(first, "worker_id")
    second_worker_id = _required_report_string(second, "worker_id")
    if first_worker_id != second_worker_id:
        raise ValueError("Worker identity changed across the restart")

    first_instance = _required_report_string(first, "worker_instance_ref")
    second_instance = _required_report_string(second, "worker_instance_ref")
    if first_instance == second_instance:
        raise ValueError("Worker process instance did not change; restart evidence is invalid")

    control_host = _required_report_string(first, "control_host_label")
    worker_host = _required_report_string(first, "worker_host_label")
    if control_host == worker_host:
        raise ValueError("Host A and Host B labels must be distinct")
    if _required_report_string(second, "control_host_label") != control_host:
        raise ValueError("Control host label changed between acceptance runs")
    if _required_report_string(second, "worker_host_label") != worker_host:
        raise ValueError("Worker host label changed between acceptance runs")

    for report in (first, second):
        artifact_refs = report.get("artifact_refs")
        evidence_refs = report.get("evidence_refs")
        if not isinstance(artifact_refs, list) or not isinstance(evidence_refs, list):
            raise ValueError("acceptance report is missing Artifact/Evidence references")
        if report.get("expected_input_artifact_ref") not in artifact_refs:
            raise ValueError("input Artifact reference is absent from acceptance evidence")
        if report.get("expected_output_artifact_ref") not in artifact_refs:
            raise ValueError("output Artifact reference is absent from acceptance evidence")
        if report.get("expected_evidence_ref") not in evidence_refs:
            raise ValueError("Evidence reference is absent from acceptance evidence")
        if report.get("worker_instance_ref") not in evidence_refs:
            raise ValueError(
                "Worker instance Evidence reference is absent from acceptance evidence"
            )
        if report.get("credential_material_recorded") is not False:
            raise ValueError("report does not affirm credential redaction")
        if report.get("broker_address_recorded") is not False:
            raise ValueError("report unexpectedly records broker address metadata")

    return {
        "schema": RESTART_REPORT_SCHEMA,
        "status": "pass",
        "verified_at": datetime.now(UTC).isoformat(),
        "control_host_label": control_host,
        "worker_host_label": worker_host,
        "worker_id": first_worker_id,
        "first_worker_instance_ref": first_instance,
        "second_worker_instance_ref": second_instance,
        "same_canonical_worker_identity": True,
        "worker_process_restarted": True,
        "authenticated_encrypted_transport": True,
        "artifact_evidence_round_trip": True,
        "repeat_dispatch_idempotency": True,
        "credential_material_recorded": False,
        "broker_address_recorded": False,
    }


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "worker":
            asyncio.run(_run_worker(args))
            return 0
        if args.command == "control":
            report = asyncio.run(_run_control(args))
            _write_report(args.json_report, report)
            print(json.dumps(report, sort_keys=True))
            return 0
        if args.command == "verify-restart":
            combined = _verify_restart(
                _load_report(args.first_report),
                _load_report(args.second_report),
            )
            _write_report(args.json_report, combined)
            print(json.dumps(combined, sort_keys=True))
            return 0
        raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(f"#388 two-host acceptance failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
