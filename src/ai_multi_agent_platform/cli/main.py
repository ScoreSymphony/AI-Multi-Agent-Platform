"""Command hierarchy for the canonical platform CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts.types import JsonValue

from .client import (
    APIClientError,
    ClientOptions,
    ClientResponse,
    ControlPlaneClient,
    HTTPTransport,
    TransportError,
)
from .compute import add_compute_parsers, doctor_compute, execute_compute
from .evaluation import add_evaluation_parser, execute_evaluation
from .memory_knowledge import (
    add_memory_knowledge_parsers,
    execute_knowledge,
    execute_memory,
)
from .onboarding import add_onboarding_parser, execute_onboarding
from .plugins import add_plugin_parser, execute_plugin
from .portability import add_portability_parser, execute_portability
from .profiles import CLIProfile, OwnerType, ProfileError, ProfileStore, default_config_path
from .render import Renderer
from .search import add_search_parser, execute_search
from .task_management import parse_changes, parse_updates
from .workspace import parse_json_array


@dataclass(frozen=True, slots=True)
class CommandResult:
    response: ClientResponse
    exit_code: int = 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(argv)


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    transport: HTTPTransport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    renderer = Renderer(
        json_mode=bool(args.json),
        verbose=bool(args.verbose),
        stdout=stdout,
        stderr=stderr,
    )
    previous_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = stdin
    try:
        store = ProfileStore.load(Path(args.config).expanduser())
        if args.area == "profile":
            return _profile_command(args, store, renderer)
        _, profile = store.resolve(args.profile)
        endpoint = args.endpoint or profile.endpoint
        if args.endpoint:
            profile = CLIProfile(
                endpoint=endpoint,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            )
        client = ControlPlaneClient(
            ClientOptions(
                endpoint=endpoint,
                timeout=args.timeout,
                retries=args.retries,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            ),
            transport=transport,
        )
        result = _execute(args, client, profile)
        renderer.success(result.response)
        return result.exit_code
    except (ProfileError, ValueError) as exc:
        renderer.error(ProfileError(str(exc)))
        return 2
    except APIClientError as exc:
        renderer.error(exc)
        return 3
    except TransportError as exc:
        renderer.error(exc)
        return 4
    finally:
        if stdin is not None:
            sys.stdin = previous_stdin


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform",
        description="Canonical API-first CLI for AI Multi-Agent Platform",
    )
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="CLI profile config path",
    )
    parser.add_argument("--profile", help="target profile name")
    parser.add_argument("--endpoint", help="temporary Control Plane endpoint override")
    parser.add_argument("--timeout", type=float, default=10.0, help="request timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="safe GET retry count")
    parser.add_argument("--json", action="store_true", help="stable machine-readable JSON output")
    parser.add_argument("--verbose", action="store_true", help="show diagnostic metadata")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm commands with meaningful side effects",
    )
    parser.add_argument("--client-version", action="version", version=__version__)

    areas = parser.add_subparsers(dest="root_command", required=True)
    for name, help_text in (
        ("status", "show API manifest and platform surface"),
        ("health", "show canonical health"),
        ("version", "show API and CLI versions"),
        ("doctor", "run initial Control Plane diagnostics"),
    ):
        platform_command = areas.add_parser(name, help=help_text)
        platform_command.set_defaults(area="platform", command=name)

    add_search_parser(areas)
    add_plugin_parser(areas)
    add_evaluation_parser(areas)
    add_memory_knowledge_parsers(areas)
    add_onboarding_parser(areas)
    add_portability_parser(areas)
    add_compute_parsers(areas)

    profile = areas.add_parser("profile", help="manage non-secret target profiles")
    profile.set_defaults(area="profile")
    profile_commands = profile.add_subparsers(dest="command", required=True)
    profile_commands.add_parser("list", help="list configured profiles")
    profile_show = profile_commands.add_parser("show", help="show one profile")
    profile_show.add_argument("name", nargs="?")
    profile_set = profile_commands.add_parser("set", help="create or update a profile")
    profile_set.add_argument("name")
    profile_set.add_argument("endpoint")
    profile_set.add_argument("--principal-ref")
    profile_set.add_argument("--owner-type", choices=["user", "organization", "team", "service"])
    profile_set.add_argument("--owner-id")
    profile_use = profile_commands.add_parser("use", help="select default profile")
    profile_use.add_argument("name")

    project = areas.add_parser("project", help="project resources")
    project.set_defaults(area="project")
    project_commands = project.add_subparsers(dest="command", required=True)
    _add_list_parser(project_commands, "list", "list projects")
    project_show = project_commands.add_parser("show", help="show project")
    project_show.add_argument("project_id")
    project_create = project_commands.add_parser("create", help="create project")
    project_create.add_argument("--name", required=True)
    _add_owner_arguments(project_create)
    project_create.add_argument("--idempotency-key")

    workspace = areas.add_parser("workspace", help="workspace resources")
    workspace.set_defaults(area="workspace")
    workspace_commands = workspace.add_subparsers(dest="command", required=True)
    _add_list_parser(workspace_commands, "list", "list workspaces")
    workspace_show = workspace_commands.add_parser("show", help="show workspace")
    workspace_show.add_argument("workspace_id")
    workspace_create = workspace_commands.add_parser("create", help="create workspace")
    workspace_create.add_argument("--project-id", required=True)
    workspace_create.add_argument(
        "--workspace-type",
        choices=[
            "persistent_project",
            "ephemeral_task",
            "isolated_run",
            "read_only_source",
            "cloned",
            "remote",
        ],
    )
    workspace_create.add_argument("--access-mode", choices=["read_write", "read_only"])
    workspace_create.add_argument(
        "--retention",
        choices=["persistent", "ephemeral"],
        help="workspace retention; expiry-based 'until' is not creatable by the current API",
    )
    workspace_create.add_argument("--workspace-id")
    workspace_create.add_argument(
        "--source-refs-json",
        help="JSON array of canonical workspace source-reference objects",
    )
    workspace_create.add_argument(
        "--files-json",
        help="JSON array of canonical workspace file manifest objects",
    )
    workspace_create.add_argument("--idempotency-key")

    task = areas.add_parser("task", help="task lifecycle")
    task.set_defaults(area="task")
    task_commands = task.add_subparsers(dest="command", required=True)
    _add_list_parser(task_commands, "list", "list tasks")
    task_create = task_commands.add_parser("create", help="create task")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--objective", required=True)
    task_create.add_argument("--project-id")
    _add_owner_arguments(task_create)
    task_create.add_argument("--idempotency-key")
    task_show = task_commands.add_parser("show", help="show task")
    task_show.add_argument("task_id")
    for command in ("queue", "start", "cancel", "retry"):
        lifecycle = task_commands.add_parser(command, help=f"{command} task")
        lifecycle.add_argument("task_id")
        lifecycle.add_argument("--idempotency-key")
    task_timeline = task_commands.add_parser("timeline", help="inspect canonical task timeline")
    task_timeline.add_argument("task_id")
    _add_pagination_arguments(task_timeline)
    task_management_update = task_commands.add_parser(
        "update-management",
        help="update canonical Task planning metadata",
    )
    task_management_update.add_argument("task_id")
    task_management_update.add_argument(
        "--changes-json",
        required=True,
        help="non-empty JSON object of canonical Task-management changes",
    )
    task_management_update.add_argument("--idempotency-key")
    task_management_bulk = task_commands.add_parser(
        "bulk-update-management",
        help="bulk update canonical Task planning metadata",
    )
    task_management_bulk.add_argument(
        "--updates-json",
        required=True,
        help="JSON array of {task_id, changes} objects (maximum 100)",
    )
    task_management_bulk.add_argument("--idempotency-key")

    run = areas.add_parser("run", help="run inspection")
    run.set_defaults(area="run")
    run_commands = run.add_subparsers(dest="command", required=True)
    run_list = _add_list_parser(run_commands, "list", "list runs")
    run_list.add_argument("--task-id", help="scope runs to a task")
    run_show = run_commands.add_parser("show", help="show run")
    run_show.add_argument("run_id")
    run_show.add_argument("--task-id")
    run_cancel = run_commands.add_parser("cancel", help="cancel run")
    run_cancel.add_argument("run_id")
    run_cancel.add_argument("--task-id", required=True)
    run_cancel.add_argument("--idempotency-key")

    provider = areas.add_parser("model-provider", help="model provider administration")
    provider.set_defaults(area="model-provider")
    provider_commands = provider.add_subparsers(dest="command", required=True)
    _add_list_parser(provider_commands, "list", "list model providers")
    provider_show = provider_commands.add_parser("show", help="show model provider")
    provider_show.add_argument("provider_id")
    for command in ("enable", "disable", "refresh-health"):
        action = provider_commands.add_parser(command, help=f"{command} model provider")
        action.add_argument("provider_id")
        action.add_argument("--idempotency-key")

    model = areas.add_parser("model", help="model registry inspection and administration")
    model.set_defaults(area="model")
    model_commands = model.add_subparsers(dest="command", required=True)
    _add_list_parser(model_commands, "list", "list model configurations")
    model_show = model_commands.add_parser("show", help="show model configuration")
    model_show.add_argument("model_id")
    for command in ("enable", "disable"):
        action = model_commands.add_parser(command, help=f"{command} model configuration")
        action.add_argument("model_id")
        action.add_argument("--idempotency-key")

    for area_name, collection in (
        ("plan", "plans"),
        ("step", "steps"),
        ("artifact", "artifacts"),
        ("result", "results"),
        ("capability", "capabilities"),
        ("capability-provider", "capability-providers"),
    ):
        reference = areas.add_parser(area_name, help=f"inspect canonical {collection}")
        reference.set_defaults(area="reference", collection=collection)
        commands = reference.add_subparsers(dest="command", required=True)
        _add_list_parser(commands, "list", f"list {collection}")
        show = commands.add_parser("show", help=f"show {area_name} reference")
        show.add_argument("resource_id")

    usage = areas.add_parser("usage", help="inspect canonical usage and resource accounting")
    usage_kinds = usage.add_subparsers(dest="usage_kind", required=True)
    for kind, collection in (
        ("record", "usage-records"),
        ("aggregate", "usage-aggregates"),
        ("budget", "usage-budgets"),
    ):
        usage_kind = usage_kinds.add_parser(kind, help=f"inspect canonical {collection}")
        usage_kind.set_defaults(area="reference", collection=collection)
        usage_commands = usage_kind.add_subparsers(dest="command", required=True)
        _add_list_parser(usage_commands, "list", f"list {collection}")
        usage_show = usage_commands.add_parser("show", help=f"show one {kind}")
        usage_show.add_argument("resource_id")

    extension = areas.add_parser(
        "extension",
        help="inspect explicitly registered canonical extension surfaces",
    )
    extension.set_defaults(area="extension")
    extension_commands = extension.add_subparsers(dest="command", required=True)
    extension_commands.add_parser("collections", help="list registered extension collections")
    extension_commands.add_parser("commands", help="list registered extension command names")
    extension_list = extension_commands.add_parser(
        "list",
        help="list resources from one registered extension collection",
    )
    extension_list.add_argument("collection")
    _add_pagination_arguments(extension_list)
    extension_show = extension_commands.add_parser(
        "show",
        help="show one resource from a registered extension collection",
    )
    extension_show.add_argument("collection")
    extension_show.add_argument("resource_id")
    extension_execute = extension_commands.add_parser(
        "execute",
        help="execute one explicitly registered canonical extension command",
    )
    extension_execute.add_argument("canonical_command")
    extension_execute.add_argument("resource_ref")
    extension_execute.add_argument("--payload", default="{}", help="JSON object command payload")
    extension_execute.add_argument("--idempotency-key", required=True)

    return parser


def _add_owner_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner-type", choices=["user", "organization", "team", "service"])
    parser.add_argument("--owner-id")


def _add_list_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=help_text)
    _add_pagination_arguments(parser)
    return parser


def _add_pagination_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")
    parser.add_argument("--sort", default="id")
    parser.add_argument("--direction", choices=["asc", "desc"], default="asc")
    parser.add_argument("--q")
    parser.add_argument("--filter", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--fields", help="comma-separated canonical fields")


def _profile_command(args: argparse.Namespace, store: ProfileStore, renderer: Renderer) -> int:
    if args.command == "list":
        items: list[JsonValue] = []
        for name, profile in sorted(store.profiles.items()):
            items.append(
                {
                    "name": name,
                    "current": name == store.current_profile,
                    **profile.to_json(),
                }
            )
        renderer.local_success(items)
        return 0
    if args.command == "show":
        name = args.name or store.current_profile
        try:
            profile = store.profiles[name]
        except KeyError as exc:
            raise ProfileError(f"unknown profile: {name}") from exc
        renderer.local_success(
            {"name": name, "current": name == store.current_profile, **profile.to_json()}
        )
        return 0
    if args.command == "set":
        owner_type = cast(OwnerType | None, args.owner_type)
        profile = CLIProfile(
            endpoint=args.endpoint,
            principal_ref=args.principal_ref,
            owner_type=owner_type,
            owner_id=args.owner_id,
        )
        store.set_profile(args.name, profile)
        store.save()
        renderer.local_success({"name": args.name, **profile.to_json()})
        return 0
    if args.command == "use":
        store.use(args.name)
        store.save()
        renderer.local_success({"current_profile": args.name})
        return 0
    raise ProfileError(f"unsupported profile command: {args.command}")


def _execute(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    profile: CLIProfile,
) -> CommandResult:
    if args.area == "platform":
        return _platform_command(args, client)
    if args.area == "search":
        return CommandResult(execute_search(args, client))
    if args.area == "plugin":
        return CommandResult(execute_plugin(args, client, _require_confirmation))
    if args.area == "evaluation":
        return CommandResult(execute_evaluation(args, client))
    if args.area == "onboarding":
        return CommandResult(execute_onboarding(args, client))
    if args.area == "memory":
        return CommandResult(execute_memory(args, client, _require_confirmation))
    if args.area == "knowledge":
        return CommandResult(execute_knowledge(args, client, _require_confirmation))
    if args.area == "portability":
        return CommandResult(execute_portability(args, client))
    if args.area in {"node", "worker", "worker-job"}:
        return CommandResult(execute_compute(args, client, _require_confirmation))
    if args.area == "project":
        return _project_command(args, client, profile)
    if args.area == "workspace":
        return _workspace_command(args, client)
    if args.area == "task":
        return _task_command(args, client, profile)
    if args.area == "run":
        return _run_command(args, client)
    if args.area == "model-provider":
        return _model_provider_command(args, client)
    if args.area == "model":
        return _model_command(args, client)
    if args.area == "reference":
        return _reference_command(args, client)
    if args.area == "extension":
        return _extension_command(args, client)
    raise ProfileError(f"unsupported command area: {args.area}")


def _platform_command(args: argparse.Namespace, client: ControlPlaneClient) -> CommandResult:
    if args.command == "status":
        return CommandResult(client.get("/"))
    if args.command == "health":
        return CommandResult(client.get("/health"))
    if args.command == "version":
        response = client.get("/")
        api_version: JsonValue = None
        if isinstance(response.body, dict):
            api_version = response.body.get("api_version")
        return CommandResult(
            ClientResponse(
                status=response.status,
                body={"api_version": api_version, "client_version": __version__},
                request_id=response.request_id,
                correlation_id=response.correlation_id,
                api_version=response.api_version,
            )
        )
    if args.command == "doctor":
        return _doctor(client)
    raise ProfileError(f"unsupported platform command: {args.command}")


def _doctor(client: ControlPlaneClient) -> CommandResult:
    checks: list[JsonValue] = [{"name": "cli_config", "status": "healthy"}]
    request_id: str | None = None
    correlation_id: str | None = None
    api_version: str | None = None
    blocking = False
    degraded = False
    for name, path in (("manifest", "/"), ("health", "/health"), ("readiness", "/readiness")):
        try:
            response = client.get(path, raise_for_status=False)
            request_id = response.request_id or request_id
            correlation_id = response.correlation_id or correlation_id
            api_version = response.api_version or api_version
            if response.status >= 400:
                blocking = True
                checks.append(
                    {
                        "name": name,
                        "status": "blocking",
                        "http_status": response.status,
                    }
                )
                continue
            if name == "health":
                health_status, provider_checks = _doctor_health(response.body)
                blocking = blocking or health_status == "blocking"
                degraded = degraded or health_status == "degraded"
                checks.append(
                    {
                        "name": name,
                        "status": health_status,
                        "http_status": response.status,
                    }
                )
                checks.extend(provider_checks)
                continue
            if (
                name == "readiness"
                and isinstance(response.body, dict)
                and response.body.get("ready") is not True
            ):
                blocking = True
                checks.append(
                    {
                        "name": name,
                        "status": "blocking",
                        "http_status": response.status,
                        "message": "readiness payload did not report ready=true",
                    }
                )
                continue
            checks.append({"name": name, "status": "healthy", "http_status": response.status})
        except TransportError as exc:
            blocking = True
            checks.append({"name": name, "status": "blocking", "message": str(exc)})
            break
    if api_version is not None and api_version != "v1":
        blocking = True
        checks.append(
            {
                "name": "api_version",
                "status": "blocking",
                "message": f"unsupported API version: {api_version}",
            }
        )
    if not blocking:
        compute_status, compute_checks = doctor_compute(client)
        degraded = degraded or compute_status == "degraded"
        checks.extend(compute_checks)
    summary = "blocking" if blocking else "degraded" if degraded else "healthy"
    return CommandResult(
        ClientResponse(
            status=503 if blocking else 200,
            body={"summary": summary, "checks": checks},
            request_id=request_id,
            correlation_id=correlation_id,
            api_version=api_version,
        ),
        exit_code=4 if blocking else 1 if degraded else 0,
    )


def _doctor_health(body: JsonValue) -> tuple[str, list[JsonValue]]:
    if not isinstance(body, dict):
        return "blocking", [
            {
                "name": "health_schema",
                "status": "blocking",
                "message": "health payload must be a JSON object",
            }
        ]

    ready = body.get("ready")
    providers = body.get("providers")
    if not isinstance(ready, bool) or not isinstance(providers, list):
        return "blocking", [
            {
                "name": "health_schema",
                "status": "blocking",
                "message": "health payload must contain boolean ready and provider list",
            }
        ]

    overall = "healthy" if ready else "blocking"
    checks: list[JsonValue] = []
    for provider in providers:
        if not isinstance(provider, dict):
            overall = "blocking"
            checks.append(
                {
                    "name": "provider_health",
                    "status": "blocking",
                    "message": "provider health entry must be a JSON object",
                }
            )
            continue

        provider_id = provider.get("id")
        provider_type = provider.get("type")
        status = provider.get("status")
        available = provider.get("available")
        if (
            not isinstance(provider_id, str)
            or not isinstance(provider_type, str)
            or not isinstance(status, str)
            or not isinstance(available, bool)
            or status not in {"healthy", "degraded", "unknown", "unavailable"}
        ):
            overall = "blocking"
            checks.append(
                {
                    "name": "provider_health",
                    "status": "blocking",
                    "message": "provider health entry does not match the canonical schema",
                }
            )
            continue

        if not available or status == "unavailable":
            check_status = "blocking"
            overall = "blocking"
        elif status in {"degraded", "unknown"}:
            check_status = "degraded"
            if overall == "healthy":
                overall = "degraded"
        else:
            check_status = "healthy"
        checks.append(
            {
                "name": "provider_health",
                "status": check_status,
                "provider_id": provider_id,
                "provider_type": provider_type,
                "provider_status": status,
                "available": available,
            }
        )
    return overall, checks


def _project_command(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    profile: CLIProfile,
) -> CommandResult:
    if args.command == "list":
        return CommandResult(client.get("/projects", query=_page_query(args)))
    if args.command == "show":
        return CommandResult(client.get(f"/projects/{_segment(args.project_id)}"))
    if args.command == "create":
        owner_type, owner_id = _owner(args.owner_type, args.owner_id, profile)
        body: dict[str, JsonValue] = {
            "name": args.name,
            "owner_type": owner_type,
            "owner_id": owner_id,
        }
        return CommandResult(
            client.post("/projects", body=body, idempotency_key=args.idempotency_key)
        )
    raise ProfileError(f"unsupported project command: {args.command}")


def _workspace_command(args: argparse.Namespace, client: ControlPlaneClient) -> CommandResult:
    if args.command == "list":
        return CommandResult(client.get("/workspaces", query=_page_query(args)))
    if args.command == "show":
        return CommandResult(client.get(f"/workspaces/{_segment(args.workspace_id)}"))
    if args.command == "create":
        workspace_body: dict[str, JsonValue] = {"project_id": args.project_id}
        for field in ("workspace_type", "access_mode", "retention", "workspace_id"):
            value = getattr(args, field)
            if value is not None:
                workspace_body[field] = value
        if args.source_refs_json is not None:
            workspace_body["source_refs"] = parse_json_array(
                args.source_refs_json,
                "--source-refs-json",
            )
        if args.files_json is not None:
            workspace_body["files"] = parse_json_array(args.files_json, "--files-json")
        return CommandResult(
            client.post(
                "/workspaces",
                body=workspace_body,
                idempotency_key=args.idempotency_key,
            )
        )
    raise ProfileError(f"unsupported workspace command: {args.command}")


def _task_command(
    args: argparse.Namespace,
    client: ControlPlaneClient,
    profile: CLIProfile,
) -> CommandResult:
    if args.command == "list":
        return CommandResult(client.get("/tasks", query=_page_query(args)))
    if args.command == "create":
        owner_type, owner_id = _owner(args.owner_type, args.owner_id, profile)
        create_body: dict[str, JsonValue] = {
            "title": args.title,
            "objective": args.objective,
            "owner_type": owner_type,
            "owner_id": owner_id,
        }
        if args.project_id is not None:
            create_body["project_id"] = args.project_id
        return CommandResult(
            client.post("/tasks", body=create_body, idempotency_key=args.idempotency_key)
        )
    if args.command == "show":
        return CommandResult(client.get(f"/tasks/{_segment(args.task_id)}"))
    if args.command == "update-management":
        _require_confirmation(args, "update task management", args.task_id)
        changes = parse_changes(args.changes_json)
        update_body: dict[str, JsonValue] = {"resource_ref": args.task_id, **changes}
        return CommandResult(
            client.post(
                "/commands/task-management.update",
                body=update_body,
                idempotency_key=args.idempotency_key,
            )
        )
    if args.command == "bulk-update-management":
        _require_confirmation(args, "bulk update task management", "tasks")
        updates = parse_updates(args.updates_json)
        bulk_body: dict[str, JsonValue] = {"resource_ref": "tasks", "updates": updates}
        return CommandResult(
            client.post(
                "/commands/task-management.bulk-update",
                body=bulk_body,
                idempotency_key=args.idempotency_key,
            )
        )
    if args.command in {"queue", "start", "cancel", "retry"}:
        if args.command == "cancel":
            _require_confirmation(args, "cancel task", args.task_id)
        return CommandResult(
            client.post(
                f"/tasks/{_segment(args.task_id)}:{args.command}",
                idempotency_key=args.idempotency_key,
            )
        )
    if args.command == "timeline":
        return CommandResult(
            client.get(
                f"/tasks/{_segment(args.task_id)}/timeline",
                query=_page_query(args),
            )
        )
    raise ProfileError(f"unsupported task command: {args.command}")


def _run_command(args: argparse.Namespace, client: ControlPlaneClient) -> CommandResult:
    if args.command == "list":
        path = f"/tasks/{_segment(args.task_id)}/runs" if args.task_id else "/runs"
        return CommandResult(client.get(path, query=_page_query(args)))
    if args.command == "show":
        path = (
            f"/tasks/{_segment(args.task_id)}/runs/{_segment(args.run_id)}"
            if args.task_id
            else f"/runs/{_segment(args.run_id)}"
        )
        return CommandResult(client.get(path))
    if args.command == "cancel":
        _require_confirmation(args, "cancel run", args.run_id)
        return CommandResult(
            client.post(
                f"/tasks/{_segment(args.task_id)}/runs/{_segment(args.run_id)}:cancel",
                idempotency_key=args.idempotency_key,
            )
        )
    raise ProfileError(f"unsupported run command: {args.command}")


def _model_provider_command(
    args: argparse.Namespace,
    client: ControlPlaneClient,
) -> CommandResult:
    if args.command == "list":
        return CommandResult(client.get("/model-providers", query=_page_query(args)))
    if args.command == "show":
        return CommandResult(client.get(f"/model-providers/{_segment(args.provider_id)}"))
    if args.command in {"enable", "disable", "refresh-health"}:
        if args.command in {"enable", "disable"}:
            _require_confirmation(args, f"{args.command} model provider", args.provider_id)
        return CommandResult(
            client.post(
                f"/model-providers/{_segment(args.provider_id)}:{args.command}",
                idempotency_key=args.idempotency_key,
            )
        )
    raise ProfileError(f"unsupported model-provider command: {args.command}")


def _model_command(args: argparse.Namespace, client: ControlPlaneClient) -> CommandResult:
    if args.command == "list":
        return CommandResult(client.get("/models", query=_page_query(args)))
    if args.command == "show":
        return CommandResult(client.get(f"/models/{_segment(args.model_id)}"))
    if args.command in {"enable", "disable"}:
        _require_confirmation(args, f"{args.command} model", args.model_id)
        return CommandResult(
            client.post(
                f"/models/{_segment(args.model_id)}:{args.command}",
                idempotency_key=args.idempotency_key,
            )
        )
    raise ProfileError(f"unsupported model command: {args.command}")


def _reference_command(args: argparse.Namespace, client: ControlPlaneClient) -> CommandResult:
    collection = str(args.collection)
    if args.command == "list":
        return CommandResult(client.get(f"/{collection}", query=_page_query(args)))
    if args.command == "show":
        return CommandResult(client.get(f"/{collection}/{_segment(args.resource_id)}"))
    raise ProfileError(f"unsupported {collection} command: {args.command}")


def _extension_command(args: argparse.Namespace, client: ControlPlaneClient) -> CommandResult:
    specification = client.get("/openapi.json")
    collections = _extension_names(specification, "x-registered-extension-collections")
    commands = _extension_names(specification, "x-registered-extension-commands")
    if args.command == "collections":
        return CommandResult(_name_page(specification, collections))
    if args.command == "commands":
        return CommandResult(_name_page(specification, commands))
    if args.command == "execute":
        command = str(args.canonical_command)
        if command not in commands:
            raise ProfileError(f"canonical extension command is not registered: {command}")
        body = {"resource_ref": str(args.resource_ref), **_json_object(str(args.payload))}
        return CommandResult(
            client.post(
                f"/commands/{_segment(command)}",
                body=body,
                idempotency_key=str(args.idempotency_key),
            )
        )

    collection = str(args.collection)
    if collection not in collections:
        raise ProfileError(f"canonical extension collection is not registered: {collection}")
    if args.command == "list":
        return CommandResult(client.get(f"/{_segment(collection)}", query=_page_query(args)))
    if args.command == "show":
        return CommandResult(client.get(f"/{_segment(collection)}/{_segment(args.resource_id)}"))
    raise ProfileError(f"unsupported extension command: {args.command}")


def _json_object(raw: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError("--payload must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProfileError("--payload must decode to a JSON object")
    return cast(dict[str, JsonValue], decoded)


def _extension_names(response: ClientResponse, field: str) -> tuple[str, ...]:
    body = response.body
    if not isinstance(body, dict):
        raise TransportError("Control Plane OpenAPI response must be a JSON object")
    raw_names = body.get(field)
    if not isinstance(raw_names, list) or not all(isinstance(item, str) for item in raw_names):
        raise TransportError(f"Control Plane OpenAPI is missing canonical field: {field}")
    return tuple(cast(str, item) for item in raw_names)


def _name_page(response: ClientResponse, names: tuple[str, ...]) -> ClientResponse:
    items: list[JsonValue] = [{"name": name} for name in names]
    return ClientResponse(
        status=response.status,
        body={"items": items, "total": len(items)},
        request_id=response.request_id,
        correlation_id=response.correlation_id,
        api_version=response.api_version,
    )


def _require_confirmation(args: argparse.Namespace, action: str, resource_ref: str) -> None:
    if bool(args.yes):
        return
    if not sys.stdin.isatty():
        raise ProfileError(f"{action} {resource_ref} requires confirmation; re-run with --yes")
    answer = input(f"Confirm {action} {resource_ref}? [y/N] ")
    if answer.strip().casefold() not in {"y", "yes"}:
        raise ProfileError("operation cancelled")


def _page_query(args: argparse.Namespace) -> dict[str, str]:
    query = {"limit": str(args.limit), "sort": args.sort, "direction": args.direction}
    if args.cursor:
        query["cursor"] = args.cursor
    if args.q:
        query["q"] = args.q
    if args.fields:
        query["fields"] = args.fields
    for raw_filter in args.filter:
        field, separator, value = raw_filter.partition("=")
        if not separator or not field or not value:
            raise ProfileError("--filter must use FIELD=VALUE")
        query[f"filter[{field}]"] = value
    return query


def _segment(value: str) -> str:
    return quote(value, safe="")


def _owner(
    owner_type_value: str | None,
    owner_id_value: str | None,
    profile: CLIProfile,
) -> tuple[str, str]:
    owner_type = owner_type_value or profile.owner_type
    owner_id = owner_id_value or profile.owner_id
    if owner_type is None or owner_id is None:
        raise ProfileError(
            "owner context is required; pass --owner-type/--owner-id or configure it in the profile"
        )
    return owner_type, owner_id


if __name__ == "__main__":
    raise SystemExit(main())
