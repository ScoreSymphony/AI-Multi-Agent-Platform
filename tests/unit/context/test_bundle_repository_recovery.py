"""Context bundle repository recovery coverage originating in issue #650."""

from __future__ import annotations

import json
from pathlib import Path

from ai_multi_agent_platform.context import JsonContextBundleRepository


def test_context_repository_quarantines_corrupt_bundle_store_on_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "context" / "bundles.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bundles": [
                    {
                        "context_bundle_id": "ctx-corrupt",
                        "task_id": "task-1",
                        "run_id": "run-1",
                        "agent_id": "agent-1",
                        "agent_revision": 1,
                        "resolver_version": "context-resolver/v1",
                        "policy_version": "context-policy/v1",
                        "budget": {"max_tokens": None, "max_bytes": None, "max_items": None},
                        "entries": [],
                        "omissions": [],
                        "usage": {
                            "selected_tokens": 0,
                            "selected_bytes": 0,
                            "selected_items": 0,
                            "omitted_items": 0,
                        },
                        "context_bundle_digest": "tampered-digest",
                        "created_at": "2025-01-01T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repository = JsonContextBundleRepository(store_path)
    assert repository.list_bundles() == ()
    assert not store_path.exists()
    quarantined = list(store_path.parent.glob("bundles.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8")
