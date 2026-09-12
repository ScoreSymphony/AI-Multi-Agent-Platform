from __future__ import annotations

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.search import document_from_resource


def test_registered_agent_shape_maps_to_canonical_search_document() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    document = document_from_resource(
        {
            "id": "agent_researcher",
            "type": "agent",
            "project_id": project_id,
            "workspace_id": workspace_id,
            "owner_ref": {"type": "user", "id": "owner-1"},
            "current_revision": 2,
            "updated_at": "2026-09-03T16:00:00+00:00",
            "labels": ["research", "production"],
            "revision": {
                "profile": {
                    "name": "Research Agent",
                    "role": "researcher",
                    "description": "Finds canonical resources safely.",
                }
            },
        },
        collection="agents",
    )

    assert document.resource_type == "agent"
    assert document.resource_id == "agent_researcher"
    assert document.title == "Research Agent"
    assert document.summary == "Finds canonical resources safely."
    assert document.project_id == project_id
    assert document.workspace_id == workspace_id
    assert document.owner_type == "user"
    assert document.owner_id == "owner-1"
    assert document.version == "2"
    assert document.tags == ("research", "production")
    assert "researcher" in document.keywords
    assert document.canonical_ref == "/api/v1/agents/agent_researcher"
    assert document.provenance == {
        "indexed_from": "canonical-control-plane",
        "collection": "agents",
    }
