"""CLI surface for canonical issue #79 portability workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient
from .profiles import ProfileError


def add_portability_parser(areas: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    portability = areas.add_parser("portability", help="portable import and export workflows")
    portability.set_defaults(area="portability")
    commands = portability.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="export canonical resources into a package")
    export.add_argument(
        "--resource",
        action="append",
        required=True,
        metavar="TYPE:ID",
        help="canonical resource selection; repeat for multiple resources",
    )
    export.add_argument(
        "--metadata-json",
        help="optional JSON object embedded as provenance metadata",
    )
    export.add_argument("--idempotency-key")

    validate = commands.add_parser("validate", help="validate and register a portable package")
    validate.add_argument("package_file", type=Path)
    validate.add_argument("--idempotency-key")

    preview = commands.add_parser("preview", help="dry-run a registered portable package")
    preview.add_argument("package_id")
    preview.add_argument("--idempotency-key")

    import_command = commands.add_parser("import", help="execute one validated import preview")
    import_command.add_argument("preview_id")
    import_command.add_argument("--idempotency-key")

    package = commands.add_parser("package", help="show one registered portable package")
    package.add_argument("package_id")

    preview_show = commands.add_parser("preview-show", help="show one stored import preview")
    preview_show.add_argument("preview_id")

    report = commands.add_parser("report", help="show one completed import report")
    report.add_argument("report_id")


def execute_portability(
    args: argparse.Namespace,
    client: ControlPlaneClient,
) -> ClientResponse:
    if args.command == "export":
        resources = [_parse_resource_selection(value) for value in args.resource]
        body: dict[str, JsonValue] = {
            "resource_ref": "portability",
            "resources": resources,
        }
        if args.metadata_json is not None:
            body["metadata"] = _parse_json_object(args.metadata_json, "--metadata-json")
        return client.post(
            "/commands/portability.export",
            body=body,
            idempotency_key=args.idempotency_key,
        )

    if args.command == "validate":
        package = _load_json_object(args.package_file)
        return client.post(
            "/commands/portability.package.validate",
            body={"resource_ref": "portability", "package": package},
            idempotency_key=args.idempotency_key,
        )

    if args.command == "preview":
        return client.post(
            "/commands/portability.preview",
            body={"resource_ref": args.package_id},
            idempotency_key=args.idempotency_key,
        )

    if args.command == "import":
        if not args.yes:
            raise ProfileError(
                "portability import mutates canonical resources; rerun with --yes "
                "after reviewing preview"
            )
        return client.post(
            "/commands/portability.import",
            body={"resource_ref": args.preview_id},
            idempotency_key=args.idempotency_key,
        )

    if args.command == "package":
        return client.get(f"/portability-packages/{_segment(args.package_id)}")
    if args.command == "preview-show":
        return client.get(f"/portability-import-previews/{_segment(args.preview_id)}")
    if args.command == "report":
        return client.get(f"/portability-import-reports/{_segment(args.report_id)}")
    raise ProfileError(f"unsupported portability command: {args.command}")


def _parse_resource_selection(value: str) -> dict[str, JsonValue]:
    resource_type, separator, resource_id = value.partition(":")
    resource_type = resource_type.strip()
    resource_id = resource_id.strip()
    if not separator or not resource_type or not resource_id:
        raise ProfileError("--resource must use non-blank TYPE:ID syntax")
    return {"resource_type": resource_type, "resource_id": resource_id}


def _parse_json_object(value: str, label: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{label} must contain valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProfileError(f"{label} must contain a JSON object")
    return decoded


def _load_json_object(path: Path) -> dict[str, JsonValue]:
    try:
        text = path.expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"could not read portable package file {path}: {exc}") from exc
    return _parse_json_object(text, str(path))


def _segment(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


__all__ = ["add_portability_parser", "execute_portability"]
