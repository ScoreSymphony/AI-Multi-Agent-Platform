"""Operator entrypoint for the replaceable #35 TCP MessageTransport reference broker."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import ssl
import sys
from collections.abc import Sequence

from ai_multi_agent_platform.messaging import TcpMessageBroker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-message-broker",
        description="Run the self-hosted #35 TCP message broker for distributed Workers.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--transport-auth-env",
        default="PLATFORM_TRANSPORT_AUTH_KEY",
        help="Environment variable containing the HMAC key; the value is never passed on argv",
    )
    parser.add_argument("--cert-file", default=None)
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--client-ca-file", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        ssl_context = _server_ssl_context(
            cert_file=args.cert_file,
            key_file=args.key_file,
            client_ca_file=args.client_ca_file,
        )
        broker = TcpMessageBroker(
            host=str(args.host),
            port=int(args.port),
            ssl_context=ssl_context,
            authentication_key=os.environ.get(str(args.transport_auth_env)) or None,
            provider_id="distributed-message-broker",
        )
    except (OSError, ValueError) as exc:
        print(f"cannot configure message broker: {exc}", file=sys.stderr)
        return 2

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        await broker.start()
        print(f"message broker listening on {broker.host}:{broker.port}")
        await stop.wait()
        await broker.close(graceful=True)

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError) as exc:
        print(f"message broker stopped with an error: {exc}", file=sys.stderr)
        return 3
    return 0


def _server_ssl_context(
    *,
    cert_file: str | None,
    key_file: str | None,
    client_ca_file: str | None,
) -> ssl.SSLContext | None:
    configured = any(value is not None for value in (cert_file, key_file, client_ca_file))
    if not configured:
        return None
    if cert_file is None or key_file is None:
        raise ValueError("TLS broker serving requires both --cert-file and --key-file")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    if client_ca_file is not None:
        context.load_verify_locations(client_ca_file)
        context.verify_mode = ssl.CERT_REQUIRED
    return context


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
