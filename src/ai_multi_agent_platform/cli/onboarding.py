"""API-only first-run onboarding commands for the canonical platform CLI."""

from __future__ import annotations

import argparse
import json
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError

_FIRST_RUN_RESOURCE = "first-run"
_CONFIGURE_MODEL_COMMAND = "onboarding.configure-model"
_RUN_FIRST_TASK_COMMAND = "onboarding.run-first-task"


def add_onboarding_parser(
    areas: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    onboarding = areas.add_parser(
        "onboarding",
        help="inspect and advance the canonical first-run local-model journey",
    )
    onboarding.set_defaults(area="onboarding")
    commands = onboarding.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show canonical first-run readiness and guidance")

    configure = commands.add_parser(
        "configure-model",
        help="validate and persist an explicit local/self-hosted ModelProvider route",
    )
    configure.add_argument("--adapter-id", required=True)
    configure.add_argument("--provider-id", required=True)
    configure.add_argument("--model-id", required=True, dest="model_config_id")
    configure.add_argument("--provider-model", required=True)
    configure.add_argument("--base-url", required=True)
    configure.add_argument("--location", required=True, choices=["local", "self_hosted"])
    configure.add_argument("--display-name")
    configure.add_argument("--priority", type=int)
    configure.add_argument(
        "--aliases",
        help="comma-separated canonical model aliases",
    )
    configure.add_argument(
        "--capabilities-json",
        help="JSON object with canonical ModelCapabilities fields",
    )
    configure.add_argument(
        "--credential-ref-json",
        help="canonical #34 SecretReference JSON object; secret values are never accepted",
    )
    configure.add_argument("--idempotency-key")

    run = commands.add_parser(
        "run-first-task",
        help="run the selected editable General Assistant through the canonical Task path",
    )
    run.add_argument("--objective", required=True)
    run.add_argument("--title")
    run.add_argument("--project-id")
    run.add_argument("--workspace-id")
    run.add_argument("--agent-id")
    run.add_argument("--idempotency-key")


def execute_onboarding(args: argparse.Namespace, client: ControlPlaneClient) -> ClientResponse:
    if args.command == "status":
        return client.get(f"/onboarding/{_FIRST_RUN_RESOURCE}")
    if args.command == "configure-model":
        body: dict[str, JsonValue] = {
            "resource_ref": _FIRST_RUN_RESOURCE,
            "adapter_id": args.adapter_id,
            "provider_id": args.provider_id,
            "model_config_id": args.model_config_id,
            "provider_model": args.provider_model,
            "base_url": args.base_url,
            "location": args.location,
        }
        if args.display_name is not None:
            body["display_name"] = args.display_name
        if args.priority is not None:
            body["priority"] = args.priority
        if args.aliases is not None:
            aliases = [item.strip() for item in args.aliases.split(",") if item.strip()]
            if not aliases:
                raise ProfileError("--aliases must contain at least one non-blank alias")
            body["aliases"] = aliases
        if args.capabilities_json is not None:
            body["capabilities"] = _json_object(
                args.capabilities_json,
                "--capabilities-json",
            )
        if args.credential_ref_json is not None:
            body["credential_ref"] = _json_object(
                args.credential_ref_json,
                "--credential-ref-json",
            )
        return client.post(
            f"/commands/{_CONFIGURE_MODEL_COMMAND}",
            body=body,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "run-first-task":
        task_body: dict[str, JsonValue] = {
            "resource_ref": _FIRST_RUN_RESOURCE,
            "objective": args.objective,
        }
        for field in ("title", "project_id", "workspace_id", "agent_id"):
            value = getattr(args, field)
            if value is not None:
                task_body[field] = value
        return client.post(
            f"/commands/{_RUN_FIRST_TASK_COMMAND}",
            body=task_body,
            idempotency_key=args.idempotency_key,
        )
    raise ProfileError(f"unsupported onboarding command: {args.command}")


def _json_object(raw: str, option: str) -> dict[str, JsonValue]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{option} must contain valid JSON") from exc
    if not isinstance(value, dict):
        raise ProfileError(f"{option} must contain a JSON object")
    return cast(dict[str, JsonValue], value)
