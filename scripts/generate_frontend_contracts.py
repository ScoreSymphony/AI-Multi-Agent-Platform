#!/usr/bin/env python3
"""Generate stable frontend transport DTOs from canonical Control Plane OpenAPI."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUTPUT = ROOT / "frontend" / "src" / "api" / "generated" / "control-plane-v1.ts"
GENERATOR_VERSION = 1
SCHEMA_EXPORTS = (
    ("APIError", "APIError"),
    ("Owner", "Owner"),
    ("APIManifest", "APIManifest"),
    ("HealthProviderStatus", "HealthProviderStatus"),
    ("HealthStatus", "HealthStatus"),
    ("Project", "Project"),
    ("WorkspaceSourceRef", "WorkspaceSourceRef"),
    ("WorkspaceSourceRefInput", "WorkspaceSourceRefInput"),
    ("WorkspaceFileInput", "WorkspaceFileInput"),
    ("IdentityOnlyWorkspace", "IdentityOnlyWorkspace"),
    ("CanonicalWorkspace", "CanonicalWorkspace"),
    # The established Workspace schema remains available for compatibility.
    # Actual v1 workspace responses use the lifecycle-aware transport union.
    ("Workspace", "WorkspaceTransport"),
    ("TaskResponsibility", "TaskResponsibility"),
    ("AgentAssignment", "AgentAssignment"),
    ("AgentAssignmentInput", "AgentAssignmentInput"),
    ("TaskDependency", "TaskDependency"),
    ("TaskDependencyInput", "TaskDependencyInput"),
    ("Task", "Task"),
    ("RunError", "RunError"),
    ("Run", "Run"),
    ("ModelCapabilities", "ModelCapabilities"),
    ("Model", "Model"),
    ("ModelProvider", "ModelProvider"),
    ("CreateProjectRequest", "CreateProjectRequest"),
    ("CreateWorkspaceRequest", "CreateWorkspaceRequest"),
    ("CreateTaskRequest", "CreateTaskRequest"),
)

_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _load_openapi() -> dict[str, Any]:
    sys.path.insert(0, str(SRC))
    from ai_multi_agent_platform.control_plane import build_openapi

    specification = build_openapi()
    if specification.get("openapi") != "3.1.0":
        raise RuntimeError("frontend DTO generation requires canonical OpenAPI 3.1")
    if specification.get("x-platform-api-version") != "v1":
        raise RuntimeError("frontend DTO generation requires the canonical v1 Control Plane")
    return specification


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return repr(value)
    raise TypeError(f"unsupported OpenAPI literal: {value!r}")


def _deduplicate(parts: list[str]) -> list[str]:
    result: list[str] = []
    for part in parts:
        if part not in result:
            result.append(part)
    return result


def _property_name(name: str) -> str:
    return name if _IDENTIFIER.fullmatch(name) else json.dumps(name)


def _render_schema(schema: Any, level: int = 0) -> str:
    if not isinstance(schema, dict):
        return "JsonValue"

    reference = schema.get("$ref")
    if isinstance(reference, str):
        prefix = "#/components/schemas/"
        if not reference.startswith(prefix):
            raise ValueError(f"unsupported non-local OpenAPI reference: {reference}")
        referenced = reference.removeprefix(prefix)
        return "Workspace" if referenced == "WorkspaceTransport" else referenced

    if "const" in schema:
        return _literal(schema["const"])

    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return " | ".join(_deduplicate([_literal(value) for value in enum]))

    for keyword, operator in (("oneOf", " | "), ("anyOf", " | "), ("allOf", " & ")):
        variants = schema.get(keyword)
        if isinstance(variants, list) and variants:
            return operator.join(
                _deduplicate([_render_schema(variant, level) for variant in variants])
            )

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        parts = []
        for item in schema_type:
            branch = dict(schema)
            branch["type"] = item
            parts.append(_render_schema(branch, level))
        return " | ".join(_deduplicate(parts))

    if schema_type == "null":
        return "null"
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "array":
        item_type = _render_schema(schema.get("items", {}), level)
        if " | " in item_type or " & " in item_type:
            item_type = f"({item_type})"
        return f"{item_type}[]"

    properties = schema.get("properties")
    if schema_type == "object" or isinstance(properties, dict):
        additional = schema.get("additionalProperties", False)
        if not isinstance(properties, dict) or not properties:
            if additional is True:
                return "Record<string, JsonValue>"
            if isinstance(additional, dict):
                return f"Record<string, {_render_schema(additional, level)}>"
            return "Record<string, never>"

        required_raw = schema.get("required", [])
        required = set(required_raw) if isinstance(required_raw, list) else set()
        indent = "  " * level
        child_indent = "  " * (level + 1)
        lines = ["{"]
        for name in sorted(properties):
            optional = "" if name in required else "?"
            rendered = _render_schema(properties[name], level + 1)
            lines.append(f"{child_indent}{_property_name(name)}{optional}: {rendered};")
        lines.append(f"{indent}}}")
        # For named DTOs, declared properties are the compatibility surface.
        # OpenAPI additionalProperties still documents server extensibility, but
        # adding a TypeScript index signature would make optional properties
        # incompatible with undefined and needlessly break v1 fixture objects.
        return "\n".join(lines)

    return "JsonValue"


def render_types(specification: dict[str, Any]) -> str:
    components = specification.get("components")
    if not isinstance(components, dict):
        raise RuntimeError("canonical OpenAPI has no components object")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        raise RuntimeError("canonical OpenAPI has no components.schemas object")

    missing = [schema_name for _, schema_name in SCHEMA_EXPORTS if schema_name not in schemas]
    if missing:
        raise RuntimeError(f"canonical OpenAPI is missing generated DTO schemas: {missing}")

    lines = [
        "// GENERATED FILE - DO NOT EDIT.",
        "// Source: ai_multi_agent_platform.control_plane.build_openapi()",
        f"// Generator: scripts/generate_frontend_contracts.py v{GENERATOR_VERSION}",
        "",
        "export type JsonPrimitive = string | number | boolean | null;",
        ("export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };"),
        "",
    ]
    for export_name, schema_name in SCHEMA_EXPORTS:
        rendered = _render_schema(schemas[schema_name])
        lines.append(f"export type {export_name} = {rendered};")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the committed generated DTO file differs from canonical OpenAPI",
    )
    args = parser.parse_args()

    generated = render_types(_load_openapi())
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
        if current != generated:
            print(
                "frontend transport DTOs are stale; run "
                "`python scripts/generate_frontend_contracts.py`",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
