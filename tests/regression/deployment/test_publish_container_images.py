from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-container-images.yml"
LEGACY_WORKFLOW = ROOT / ".github" / "workflows" / "runtime-container-images.yml"
CATALOG_COMPOSE = ROOT / "deploy" / "docker" / "docker-compose.hostinger-catalog-candidate.yml"
RUNBOOK = ROOT / "docs" / "operations" / "HOSTINGER_ONE_CLICK.md"


def test_runtime_image_publication_has_one_maintained_workflow() -> None:
    assert WORKFLOW.is_file()
    assert not LEGACY_WORKFLOW.exists()


def test_runtime_image_workflow_is_publish_only_and_least_privilege() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "source_sha:" in workflow
    assert "exact 40-character lowercase commit SHA" in workflow
    assert '      - "v*.*.*"' in workflow
    assert "pull_request:" not in workflow
    assert "contents: read" in workflow
    assert "packages: write" in workflow
    assert "actions: write" not in workflow
    assert "id-token: write" not in workflow
    assert "secrets.GITHUB_TOKEN" in workflow
    assert "persist-credentials: false" in workflow


def test_runtime_image_workflow_builds_all_components_before_publication() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "  build:" in workflow
    assert "  publish:" in workflow
    assert "      - build" in workflow
    for component, dockerfile in (
        ("control-plane", "deploy/docker/control-plane.Dockerfile"),
        ("web", "deploy/docker/web.Dockerfile"),
        ("hostinger-gateway", "deploy/docker/hostinger-gateway.Dockerfile"),
    ):
        assert f"component: {component}" in workflow
        assert dockerfile in workflow

    assert "push: false" in workflow
    assert "push: true" in workflow
    assert "platforms: linux/amd64" in workflow


def test_runtime_image_workflow_preserves_immutable_source_identity() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'sha_tag="sha-${source_sha}"' in workflow
    assert "manual source mismatch" in workflow
    assert "main publication source mismatch" in workflow
    assert "tag event source mismatch" in workflow
    assert "immutable SHA tag already exists" in workflow
    assert "release tag already exists and will not be rewritten" in workflow
    assert "org.opencontainers.image.source=https://github.com/ScoreSymphony/AI-Multi-Agent-Platform" in workflow
    assert "org.opencontainers.image.revision=${{ needs.prepare.outputs.source_sha }}" in workflow
    assert "org.opencontainers.image.licenses=MIT" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow


def test_runtime_image_workflow_records_refs_and_digests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Record publication evidence" in workflow
    assert "steps.publish.outputs.digest" in workflow
    assert "immutable ref:" in workflow
    assert "source commit:" in workflow
    assert "release ref:" in workflow


def test_catalog_candidate_uses_maintained_ghcr_image_family() -> None:
    compose = CATALOG_COMPOSE.read_text(encoding="utf-8")

    for image in (
        "ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane",
        "ghcr.io/scoresymphony/ai-multi-agent-platform-web",
        "ghcr.io/scoresymphony/ai-multi-agent-platform-hostinger-gateway",
    ):
        assert image in compose
    assert "build:" not in compose


def test_hostinger_runbook_documents_exact_candidate_publication_and_visibility_gate() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "exact 40-character source commit SHA" in runbook
    assert "immutable SHA tag" in runbook
    assert "will not overwrite" in runbook
    assert "workflow summary" in runbook
    assert "publicly pullable without credentials" in runbook
    assert "docker manifest inspect" in runbook
    assert "single maintained publication workflow" in runbook
