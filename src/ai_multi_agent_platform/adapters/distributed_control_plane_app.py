"""Advanced distributed Control-Plane entrypoint for issue #240.

The normal #39 single-node composition remains the fallback. This adapter opts the same canonical
Task/Run kernel into #14 distributed execution, validates one executable #240 deployment profile,
then exposes the authenticated Worker protocol and #35 network transport at the outer boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
from collections.abc import Sequence
from typing import Any, cast

from ai_multi_agent_platform.deployment.advanced_profiles import (
    AdvancedDeploymentProfile,
    load_advanced_deployment_profile,
)
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.distributed_admin import register_distributed_worker_admin
from ai_multi_agent_platform.deployment.distributed_control_plane import build_worker_protocol_app
from ai_multi_agent_platform.deployment.server import main as run_server
from ai_multi_agent_platform.deployment.single_node import SingleNodeDeployment
from ai_multi_agent_platform.distributed import (
    DistributedExecutorArtifactProvider,
    register_distributed_control_plane,
)
from ai_multi_agent_platform.messaging import TcpMessageTransport

from .single_node_app import build_default_single_node_deployment

_PROFILE_ENV = "PLATFORM_DISTRIBUTED_PROFILE"


def build_distributed_control_plane_deployment(
    config: SingleNodeConfig,
    *,
    profile_path: str | None = None,
) -> SingleNodeDeployment:
    """Build one Control Plane whose canonical Runs and Worker protocol share one runtime."""

    resolved_profile = profile_path or os.environ.get(_PROFILE_ENV)
    if resolved_profile is None:
        raise ValueError("distributed Control Plane requires an explicit deployment profile")
    profile = load_advanced_deployment_profile(resolved_profile)
    _validate_runnable_profile(profile)

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

    # The shipped distributed server exposes the already-existing canonical #14 compute resources
    # and admin commands. Runtime inspection/drain/maintenance therefore use the same northbound
    # Control Plane as the rest of the platform rather than a deployment-private shortcut.
    register_distributed_control_plane(deployment.control_plane, runtime)
    register_distributed_worker_admin(
        deployment.control_plane,
        profile=profile,
        authentication=deployment.authentication,
        authorization=deployment.authorization,
    )

    # Capability discovery exposes one provider for the distributed artifact operation. Worker
    # placement is intentionally deferred to the canonical scheduler on each invocation so current
    # drain/maintenance/liveness state remains authoritative without a mirrored provider registry.
    asyncio.run(
        deployment.capabilities.register_provider(
            DistributedExecutorArtifactProvider(
                runtime,
                workspace_bindings=deployment.run_workspace_bindings,
            )
        )
    )

    app, _service = build_worker_protocol_app(
        downstream=deployment.app,
        runtime=runtime,
        authentication=deployment.authentication,
        authorization=deployment.authorization,
        transport=transport,
        workspaces=deployment.workspaces,
        files=deployment.files,
        kernel=deployment.kernel,
    )
    # ``SingleNodeDeployment`` intentionally types its Stage-1 app as ControlPlaneASGI. The
    # advanced adapter wraps that same ASGI app without changing the Stage-1 contract.
    cast(Any, deployment).app = app
    return deployment


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    profile_parser = argparse.ArgumentParser(add_help=False)
    profile_parser.add_argument("--profile", default=None)
    profile_args, server_args = profile_parser.parse_known_args(raw_args)
    profile_path = str(profile_args.profile) if profile_args.profile is not None else None
    profile_path = profile_path or os.environ.get(_PROFILE_ENV)
    if not profile_path:
        print(
            "distributed Control Plane requires --profile or PLATFORM_DISTRIBUTED_PROFILE",
            file=sys.stderr,
        )
        return 2

    def builder(config: SingleNodeConfig) -> SingleNodeDeployment:
        return build_distributed_control_plane_deployment(config, profile_path=profile_path)

    try:
        return run_server(server_args, deployment_builder=builder)
    except ValueError as exc:
        print(f"cannot compose distributed Control Plane: {exc}", file=sys.stderr)
        return 2


def _validate_runnable_profile(profile: AdvancedDeploymentProfile) -> None:
    """Reject description-only profiles that the shipped Worker process cannot actually start."""

    for node in profile.nodes:
        if node.reporter_worker_id is None:
            raise ValueError(f"deployment node {node.binding.host_ref!r} has no reporter_worker_id")
        if node.binding.credential_reference is None:
            raise ValueError(
                f"deployment node {node.binding.host_ref!r} has no Worker credential reference"
            )


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
