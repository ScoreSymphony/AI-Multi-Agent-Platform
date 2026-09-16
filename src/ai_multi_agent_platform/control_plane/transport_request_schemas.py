"""Request-only schema refinements for generated Control Plane transport DTOs."""

from __future__ import annotations

from typing import Any


def augment_transport_request_schemas(specification: dict[str, Any]) -> dict[str, Any]:
    """Describe request defaults without weakening canonical response DTOs."""

    components = specification.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.update(
        {
            "WorkspaceSourceRefInput": {
                "type": "object",
                "required": ["kind", "ref"],
                "properties": {
                    "kind": {"type": "string"},
                    "ref": {"type": "string"},
                    "revision": {"type": ["string", "null"]},
                    "checksum": {"type": ["string", "null"]},
                    "metadata": {"type": "object", "additionalProperties": True},
                },
                "additionalProperties": False,
            },
            "AgentAssignmentInput": {
                "type": "object",
                "required": ["kind", "id"],
                "properties": {
                    "kind": {"type": "string", "enum": ["agent", "agent_team"]},
                    "id": {"type": "string"},
                    "revision": {"type": ["integer", "null"], "minimum": 1},
                    "required": {"type": "boolean", "default": False},
                    "policy_ref": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
            "TaskDependencyInput": {
                "type": "object",
                "required": ["task_id"],
                "properties": {
                    "task_id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["depends_on", "related_to"],
                        "default": "depends_on",
                    },
                },
                "additionalProperties": False,
            },
        }
    )

    create_workspace = schemas.get("CreateWorkspaceRequest")
    if isinstance(create_workspace, dict):
        properties = create_workspace.get("properties")
        if isinstance(properties, dict):
            properties["source_refs"] = {
                "type": "array",
                "items": {"$ref": "#/components/schemas/WorkspaceSourceRefInput"},
            }

    create_task = schemas.get("CreateTaskRequest")
    if isinstance(create_task, dict):
        properties = create_task.get("properties")
        if isinstance(properties, dict):
            properties["agent_assignment"] = {
                "oneOf": [
                    {"$ref": "#/components/schemas/AgentAssignmentInput"},
                    {"type": "null"},
                ]
            }
            properties["dependencies"] = {
                "type": "array",
                "items": {"$ref": "#/components/schemas/TaskDependencyInput"},
            }

    return specification
