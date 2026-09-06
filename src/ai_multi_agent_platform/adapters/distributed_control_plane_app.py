"""Advanced distributed Control-Plane entrypoint for issue #240.

The normal #39 single-node composition remains the fallback. This adapter opts the same canonical
Task/Run kernel into #14 distributed execution, then exposes the authenticated Worker protocol and
#35 network transport at the outer deployment boundary.
"""

from __future__ import annotations

import os
import ssl
import sys
from collections.abc import Sequence
from typing import Any, cast

from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.distributed_control_plane import build_worker_protocol_app
from ai_multi_agent_platform.deployment.server import main as run_server
from ai_multi_agent_platform.deployment.single_node import SingleNodeDeployment
from ai_multi_agent_platform.messaging import TcpMessageTransport

from .single_node_app import build_default_single_node_deployment


def build_distributed_control_plane_deployment(config: SingleNodeConfig) -> SingleNodeDeployment:
    """Build one Control Plane whose canonical Runs and Worker protocol share one runtime."""

    host = os.environ.get("PLATFORM_MESSAGE_BROKER_HOST", "127.0.0.1")
    port_raw = os.environ.get("PLATFORM_MESSAGE_BROKER_PORT", "")
    if not port_raw:
        raise ValueError("PLATFORM_MESSAGE_BROKER_PORT is required for distributed serving")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("PLATFORM_MESSAGE_BROKER_PORT must be an integer") from exc

    ssl_context = _client_ssl_context()
    transport = TcpMessageTransport(
        host,
        port,
        ssl_context=ssl_context,
        server_hostname=os.environ.get("PLATFORM_MESSAGE_BROKER_SERVER_HOSTNAME") or None,
        authentication_key=os.environ.get("PLATFORM_TRANSPORT_AUTH_KEY") or None,
        provider_id="distributed-control-plane",
    )
    deployment = build_default_single_node_deployment(
        config,
        enable_distributed_execution=True,
    )
    runtime = deployment.distributed_runtime
    if runtime is None:
        raise RuntimeError("distributed deployment was built without a distributed runtime")
    app, _service = build_worker_protocol_app(
        downstream=deployment.app,
        runtime=runtime,
        authentication=deployment.authentication,
        authorization=deployment.authorization,
        transport=transport,
        workspaces=deployment.workspaces,
        files=deployment.files,
    )
    # ``SingleNodeDeployment`` intentionally types its Stage-1 app as ControlPlaneASGI. The
    # advanced adapter wraps that same ASGI app without changing the Stage-1 contract.
    cast(Any, deployment).app = app
    return deployment


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run_server(argv, deployment_builder=build_distributed_control_plane_deployment)
    except ValueError as exc:
        print(f"cannot compose distributed Control Plane: {exc}", file=sys.stderr)
        return 2


def _client_ssl_context() -> ssl.SSLContext | None:
    ca_file = os.environ.get("PLATFORM_MESSAGE_BROKER_CA_FILE") or None
    client_cert = os.environ.get("PLATFORM_MESSAGE_BROKER_CLIENT_CERT") or None
    client_key = os.environ.get("PLATFORM_MESSAGE_BROKER_CLIENT_KEY") or None
    if ca_file is None and client_cert is None and client_key is None:
        return None
    if (client_cert is None) != (client_key is None):
        raise ValueError(
            "PLATFORM_MESSAGE_BROKER_CLIENT_CERT and PLATFORM_MESSAGE_BROKER_CLIENT_KEY "
            "must be configured together"
        )
    context = ssl.create_default_context(cafile=ca_file)
    if client_cert is not None and client_key is not None:
        context.load_cert_chain(client_cert, client_key)
    return context


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
