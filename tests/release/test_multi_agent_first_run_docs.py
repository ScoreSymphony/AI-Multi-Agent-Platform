from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
FIRST_RUN = ROOT / "docs" / "product" / "MULTI_AGENT_FIRST_RUN.md"


def test_readme_is_success_first_before_deep_architecture() -> None:
    readme = README.read_text(encoding="utf-8")
    headings = (
        "## 30-second architecture",
        "## Quick Start",
        "## Run your first multi-agent task",
        "## How it works",
        "## Current maturity",
    )
    positions = [readme.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "## What is implemented today" not in readme


def test_readme_30_second_architecture_tracks_the_product_path() -> None:
    readme = README.read_text(encoding="utf-8")
    for marker in (
        "User / Web / CLI",
        "Control Plane",
        "Goal / Task",
        "Planner -> Plan / Steps",
        "Agent Team -> Models / Capabilities",
        "Execution / Workers",
        "Artifact / Result -> Verification",
        "Trace / durable history",
    ):
        assert marker in readme
    assert "replaceable" in readme
    assert "platform-owned" in readme


def test_readme_quick_start_uses_browser_first_supported_single_node_path() -> None:
    readme = README.read_text(encoding="utf-8")
    for command in (
        "pip install '.[server]'",
        "cp config/single-node.env.example .env.single-node",
        "platform-server serve",
        "npm run dev",
        "curl http://127.0.0.1:8000/api/v1/health",
        "curl http://127.0.0.1:8000/api/v1/readiness",
        "platform --endpoint http://127.0.0.1:8000 doctor",
    ):
        assert command in readme
    assert "Create your administrator account" in readme
    assert "platform-server bootstrap-admin --username ..." in readme
    assert "operator/recovery alternative" in readme
    assert "not a prerequisite" in readme
    assert "docs/operations/DEPLOYMENT.md" in readme
    assert "No GPU, paid AI/API service, Hermes, LiteLLM, MCP server or remote Worker" in readme


def test_readme_links_directly_to_official_multi_agent_first_run() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "docs/product/MULTI_AGENT_FIRST_RUN.md" in readme
    assert "platform auth login --username admin" in readme
    assert "platform onboarding status" in readme
    assert "platform onboarding run-multi-agent" in readme
    assert "researcher" in readme
    assert "developer" in readme
    assert "reviewer" in readme
    assert "parallel-ready" in readme
    assert "Artifact + Result + trace" in readme
    assert "Verification" in readme


def test_readme_keeps_deeper_docs_after_first_success() -> None:
    readme = README.read_text(encoding="utf-8")
    first_run = readme.index("## Run your first multi-agent task")
    how_it_works = readme.index("## How it works")
    assert first_run < how_it_works
    for target in (
        "docs/ARCHITECTURE_PRINCIPLES.md",
        "docs/DOMAIN_MODEL.md",
        "docs/CONTRACTS.md",
        "docs/KERNEL.md",
        "SECURITY.md",
        "docs/SECURITY_THREAT_MODEL.md",
        "docs/RELEASE_PROCESS.md",
        "docs/STATUS.md",
        "CONTRIBUTING.md",
    ):
        assert target in readme


def test_first_run_doc_uses_browser_first_single_node_and_canonical_command() -> None:
    guide = FIRST_RUN.read_text(encoding="utf-8")
    assert "pip install '.[server]'" in guide
    assert "platform-server serve" in guide
    assert "Create your administrator account" in guide
    assert "Operator/recovery account bootstrap" in guide
    assert "platform-server bootstrap-admin --username admin" in guide
    assert "not a prerequisite" in guide
    assert "onboarding.run-multi-agent-golden-path" in guide
    assert "platform auth login --username admin" in guide
    assert "platform onboarding run-multi-agent" in guide
    assert "Hermes, LiteLLM, MCP" in guide
    assert "retired Forge runtime is not part of this workflow" in guide
    assert "does **not** require" in guide
    assert "paid" in guide.lower()
