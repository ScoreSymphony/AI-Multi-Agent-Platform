from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.control_plane import build_openapi

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts" / "generate_frontend_contracts.py"


def _schemas() -> dict[str, Any]:
    components = build_openapi()["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    return schemas


def test_openapi_exposes_generated_frontend_transport_contracts() -> None:
    specification = build_openapi()
    marker = specification["x-generated-client-contract"]
    assert isinstance(marker, dict)
    assert marker["version"] == 1

    schemas = _schemas()
    assert set(schemas["APIError"]["required"]) >= {
        "code",
        "category",
        "message",
        "request_id",
        "correlation_id",
        "retryable",
    }
    assert "properties" in schemas["Workspace"]
    assert schemas["WorkspaceTransport"]["oneOf"] == [
        {"$ref": "#/components/schemas/IdentityOnlyWorkspace"},
        {"$ref": "#/components/schemas/CanonicalWorkspace"},
    ]
    assert schemas["CreateWorkspaceRequest"]["properties"]["source_refs"]["items"] == {
        "$ref": "#/components/schemas/WorkspaceSourceRefInput"
    }
    assert schemas["CreateTaskRequest"]["properties"]["agent_assignment"]["oneOf"][0] == {
        "$ref": "#/components/schemas/AgentAssignmentInput"
    }
    assert schemas["CreateTaskRequest"]["properties"]["dependencies"]["items"] == {
        "$ref": "#/components/schemas/TaskDependencyInput"
    }
    assert schemas["Task"]["properties"]["status"]["enum"] == [
        "draft",
        "ready",
        "running",
        "waiting",
        "succeeded",
        "failed",
        "cancelled",
    ]
    assert "error" in schemas["Run"]["required"]
    assert "workspace_snapshot_id" in schemas["Run"]["properties"]


def test_committed_frontend_transport_dtos_match_canonical_openapi() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
