from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.deployment import load_advanced_deployment_profile
from ai_multi_agent_platform.deployment.distributed_worker import (
    build_worker_process_from_deployment_node,
)
from ai_multi_agent_platform.distributed.worker_protocol_http import WorkerProtocolHTTPClient
from ai_multi_agent_platform.messaging import InProcessMessageTransport

_PROFILE = Path("deploy/distributed/profiles/multi-local-workers.json")
_DOC = Path("docs/ADVANCED_DEPLOYMENT.md")


def test_shipped_multi_local_workers_receive_private_workspace_roots(tmp_path: Path) -> None:
    raw = json.loads(_PROFILE.read_text(encoding="utf-8"))
    host_root = (tmp_path / "shared-host-workspaces").resolve()
    raw["nodes"][0]["deployment"]["workspace_root"] = str(host_root)
    profile_path = tmp_path / "multi-local-workers.json"
    profile_path.write_text(json.dumps(raw), encoding="utf-8")

    node = load_advanced_deployment_profile(profile_path).nodes[0]
    reporter_id = node.reporter_worker_id
    assert reporter_id is not None
    sibling_id = next(
        worker.worker_id for worker in node.workers if worker.worker_id != reporter_id
    )
    transport = InProcessMessageTransport(provider_id="issue-240-workspace-isolation")

    reporter = build_worker_process_from_deployment_node(
        node,
        worker_id=reporter_id,
        protocol=cast(WorkerProtocolHTTPClient, object()),
        transport=transport,
    )
    sibling = build_worker_process_from_deployment_node(
        node,
        worker_id=sibling_id,
        protocol=None,
        transport=transport,
    )

    assert reporter.config.workspace_root == host_root / reporter_id
    assert sibling.config.workspace_root == host_root / sibling_id
    assert reporter.config.workspace_root != sibling.config.workspace_root
    assert reporter.store.root == reporter.config.workspace_root
    assert sibling.store.root == sibling.config.workspace_root

    workspace_id = "workspace_00000000-0000-4000-8000-000000000240"
    snapshot_id = "workspace_snapshot_00000000-0000-4000-8000-000000000241"
    reporter_tree = reporter.store.root / workspace_id / snapshot_id
    sibling_tree = sibling.store.root / workspace_id / snapshot_id
    reporter_tree.mkdir(parents=True)
    sibling_tree.mkdir(parents=True)
    (reporter_tree / "marker.txt").write_text("reporter", encoding="utf-8")
    (sibling_tree / "marker.txt").write_text("sibling", encoding="utf-8")

    assert reporter_tree != sibling_tree
    assert (reporter_tree / "marker.txt").read_text(encoding="utf-8") == "reporter"
    assert (sibling_tree / "marker.txt").read_text(encoding="utf-8") == "sibling"


def test_operator_documentation_matches_secret_file_and_artifact_contract() -> None:
    text = _DOC.read_text(encoding="utf-8")
    provision_command = (
        "platform --yes worker provision <reporter_worker_id> \\\n"
        "  --secret-file <worker-token-file>"
    )
    rotate_command = (
        "platform --yes worker rotate-credential <reporter_id> \\\n"
        "  --credential-id <old_credential_id> \\\n"
        "  --secret-file <worker-token-file>"
    )

    assert provision_command in text
    assert rotate_command in text
    assert "<workspace_root>/<worker_id>/<workspace_id>/<snapshot_id>" in text
    assert "Artifact references are opaque canonical identities" in text
    assert "Artifact content is not inferred from an `artifact_*` identifier" in text
