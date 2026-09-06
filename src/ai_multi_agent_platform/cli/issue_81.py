"""Issue #81 CLI composition for optional Registry/Marketplace operations.

Registry commands are API-first and use only the canonical Control Plane surfaces
registered by ``distribution.control_plane``. Every non-Registry area delegates
unchanged to the current issue #82 CLI composition.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from .client import (
    APIClientError,
    ClientOptions,
    ControlPlaneClient,
    HTTPTransport,
    TransportError,
    UrllibTransport,
)
from .credentials import AuthenticatedTransport, CredentialStore
from .issue_82 import run_cli as issue_82_run_cli
from .profiles import CLIProfile, ProfileError, ProfileStore, default_config_path
from .registry import add_registry_parser, execute_registry
from .render import Renderer


def main() -> int:
    return run_cli()


def run_cli(
    argv: list[str] | None = None,
    *,
    transport: HTTPTransport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if _requested_area(arguments) != "registry":
        return issue_82_run_cli(
            arguments,
            transport=transport,
            stdout=stdout,
            stderr=stderr,
            stdin=stdin,
        )

    parser = _build_parser()
    args = parser.parse_args(arguments)
    renderer = Renderer(
        json_mode=bool(args.json),
        verbose=bool(args.verbose),
        stdout=stdout,
        stderr=stderr,
    )

    try:
        profiles = ProfileStore.load(Path(args.config).expanduser())
        profile_name, profile = profiles.resolve(args.profile)
        if args.endpoint is not None:
            profile = CLIProfile(
                endpoint=args.endpoint,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            )
        credentials = CredentialStore.load(profiles.path).get(profile_name)
        base_transport = transport or UrllibTransport()
        client = ControlPlaneClient(
            ClientOptions(
                endpoint=profile.endpoint,
                timeout=args.timeout,
                retries=args.retries,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            ),
            transport=AuthenticatedTransport(base_transport, credentials),
        )
        response = execute_registry(args, client, _require_confirmation)
        renderer.success(response)
        return 0
    except (ProfileError, ValueError) as exc:
        renderer.error(ProfileError(str(exc)))
        return 2
    except APIClientError as exc:
        renderer.error(exc)
        return 3
    except TransportError as exc:
        renderer.error(exc)
        return 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--profile")
    parser.add_argument("--endpoint")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm Registry activation after reviewing the exact preview/version",
    )
    areas = parser.add_subparsers(dest="area", required=True)
    add_registry_parser(areas)
    return parser


def _require_confirmation(args: argparse.Namespace, action: str, target: str) -> None:
    if not bool(args.yes):
        raise ValueError(
            f"{action} {target} has side effects; rerun with global --yes after reviewing it"
        )


def _requested_area(arguments: list[str]) -> str | None:
    options_with_value = {"--config", "--profile", "--endpoint", "--timeout", "--retries"}
    skip_next = False
    for token in arguments:
        if skip_next:
            skip_next = False
            continue
        if token in options_with_value:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None
