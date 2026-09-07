from pathlib import Path

from ai_multi_agent_platform.deployment import load_advanced_deployment_profile
from ai_multi_agent_platform.distributed import WORKSPACE_ARTIFACT_CAPABILITY_ID

PROFILES = Path("deploy/distributed/profiles")


def test_shipped_reference_workers_advertise_workspace_artifact_capability() -> None:
    for name in (
        "multi-local-workers.json",
        "remote-worker.json",
        "cpu-control-gpu-worker.json",
        "heterogeneous-three-node.json",
    ):
        profile = load_advanced_deployment_profile(PROFILES / name)
        for node in profile.nodes:
            reference_workers = [
                worker for worker in node.workers if "reference" in worker.supported_executors
            ]
            if not reference_workers:
                continue
            assert WORKSPACE_ARTIFACT_CAPABILITY_ID in node.node.capability_refs
            for worker in reference_workers:
                assert WORKSPACE_ARTIFACT_CAPABILITY_ID in worker.capability_refs
