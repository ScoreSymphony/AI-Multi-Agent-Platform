"""CLI adapter for the canonical plugin Control Plane lifecycle."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

Confirmation = Callable[[argparse.Namespace, str, str], None]


def add_plugin_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register plugin inspection and lifecycle commands over the canonical API only."""

    plugin = areas.add_parser("plugin", help="inspect and administer canonical plugins")
    plugin.set_defaults(area="plugin")
    commands = plugin.add_subparsers(dest="command", required=True)

    _add_list_parser(commands, "list", "list installed plugins")
    show = commands.add_parser("show", help="show one installed plugin")
    show.add_argument("plugin_id")

    candidate = commands.add_parser("candidate", help="inspect discovered plugin candidates")
    candidate_commands = candidate.add_subparsers(dest="candidate_command", required=True)
    _add_list_parser(candidate_commands, "list", "list discovered plugin candidates")
    candidate_show = candidate_commands.add_parser("show", help="show one plugin candidate")
    candidate_show.add_argument("plugin_id")

    install = commands.add_parser("install", help="install an inspected plugin candidate")
    _add_digest_mutation_arguments(install)

    configure = commands.add_parser("configure", help="configure an installed plugin")
    configure.add_argument("plugin_id")
    configure.add_argument(
        "--configuration-json",
        required=True,
        help="plugin configuration as a JSON object",
    )
    configure.add_argument("--idempotency-key")

    enable = commands.add_parser("enable", help="enable an installed plugin")
    _add_digest_mutation_arguments(enable)

    for command in ("disable", "refresh-health", "remove"):
        action = commands.add_parser(command, help=f"{command} an installed plugin")
        action.add_argument("plugin_id")
        action.add_argument("--idempotency-key")

    validate_update = commands.add_parser(
        "validate-update",
        help="validate an inspected candidate against the installed plugin",
    )
    _add_digest_mutation_arguments(validate_update)


def execute_plugin(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    confirm: Confirmation,
) -> ClientResponse:
    """Execute plugin commands through the versioned Control Plane contract."""

    if args.command == "list":
        return client.get("/plugins", query=_page_query(args))
    if args.command == "show":
        return client.get(f"/plugins/{_segment(args.plugin_id)}")
    if args.command == "candidate":
        if args.candidate_command == "list":
            return client.get("/plugin-candidates", query=_page_query(args))
        if args.candidate_command == "show":
            return client.get(f"/plugin-candidates/{_segment(args.plugin_id)}")
        raise ProfileError(f"unsupported plugin candidate command: {args.candidate_command}")

    plugin_id = str(args.plugin_id)
    if args.command in {"install", "configure", "enable", "disable", "remove"}:
        confirm(args, f"{args.command} plugin", plugin_id)

    body: dict[str, JsonValue] = {"resource_ref": plugin_id}
    if args.command in {"install", "enable", "validate-update"}:
        body["manifest_digest"] = str(args.manifest_digest)
    elif args.command == "configure":
        body["configuration"] = parse_configuration(args.configuration_json)
    elif args.command not in {"disable", "refresh-health", "remove"}:
        raise ProfileError(f"unsupported plugin command: {args.command}")

    return client.post(
        f"/commands/plugin.{args.command}",
        body=body,
        idempotency_key=args.idempotency_key,
    )


def parse_configuration(raw: str) -> dict[str, JsonValue]:
    """Parse a plugin configuration object without duplicating manifest validation."""

    try:
        value: JsonValue = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileError("--configuration-json must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ProfileError("--configuration-json must be a JSON object")
    return value


def _add_digest_mutation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plugin_id")
    parser.add_argument(
        "--manifest-digest",
        required=True,
        help="digest from the explicitly inspected candidate or installed plugin resource",
    )
    parser.add_argument("--idempotency-key")


def _add_list_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    return parser


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    query = {"limit": str(args.limit), "sort": str(args.sort), "direction": str(args.direction)}
    if args.cursor:
        query["cursor"] = str(args.cursor)
    return query


def _segment(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
