from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "runtime-container-images.yml"
DOC = ROOT / "docs" / "operations" / "RUNTIME_CONTAINER_IMAGES.md"


def test_runtime_image_workflow_is_publish_only_and_least_privilege() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "push:" in workflow
    assert '      - "v*.*.*"' in workflow
    assert "pull_request:" not in workflow
    assert "contents: read" in workflow
    assert "packages: write" in workflow
    assert "actions: write" not in workflow
    assert "id-token: write" not in workflow
    assert "secrets.GITHUB_TOKEN" in workflow
    assert "docker login ghcr.io" in workflow
    assert "persist-credentials: false" in workflow


def test_runtime_image_workflow_builds_all_platform_runtime_images() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected = {
        "ghcr.io/scoresymphony/ai-multi-agent-platform-control-plane":
            "deploy/docker/control-plane.Dockerfile",
        "ghcr.io/scoresymphony/ai-multi-agent-platform-web":
            "deploy/docker/web.Dockerfile",
        "ghcr.io/scoresymphony/ai-multi-agent-platform-hostinger-gateway":
            "deploy/docker/hostinger-gateway.Dockerfile",
    }
    for image, dockerfile in expected.items():
        assert image in workflow
        assert dockerfile in workflow

    assert 'sha_tag="sha-${source_sha}"' in workflow
    assert "org.opencontainers.image.source=" in workflow
    assert "org.opencontainers.image.revision=" in workflow
    assert "org.opencontainers.image.licenses=MIT" in workflow
    assert "latest" not in workflow


def test_runtime_image_workflow_rejects_tag_rewrites() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "immutable SHA tag already exists" in workflow
    assert "release tag already exists and will not be rewritten" in workflow
    assert "tag event source mismatch" in workflow
    assert "release/SHA digest mismatch" in workflow


def test_runtime_image_documentation_preserves_source_build_and_catalog_boundaries() -> None:
    doc = DOC.read_text(encoding="utf-8")

    assert "source-build Docker Compose profiles remain supported" in doc
    assert "No moving `latest` tag" in doc
    assert "exact candidate commit SHA" in doc
    assert "Hostinger Catalog/One Click integration is tracked separately in #1473" in doc
    assert "does not imply that Hostinger has accepted the project into its Catalog" in doc
