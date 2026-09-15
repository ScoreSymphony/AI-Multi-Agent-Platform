from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
FIRST_RUN = ROOT / "docs" / "product" / "MULTI_AGENT_FIRST_RUN.md"


def test_readme_links_directly_to_official_multi_agent_first_run() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "docs/product/MULTI_AGENT_FIRST_RUN.md" in readme
    assert "platform onboarding run-multi-agent" in readme


def test_first_run_doc_uses_supported_single_node_and_canonical_command() -> None:
    guide = FIRST_RUN.read_text(encoding="utf-8")
    assert "pip install '.[server]'" in guide
    assert "platform-server bootstrap-admin" in guide
    assert "platform-server serve" in guide
    assert "onboarding.run-multi-agent-golden-path" in guide
    assert "platform onboarding run-multi-agent" in guide
    assert "researcher · developer · reviewer" not in guide  # keep docs plain-text portable
    for optional in ("Hermes", "Forge", "LiteLLM"):
        assert f"does **not** require {optional}" not in guide
    assert "Hermes, Forge, LiteLLM" in guide
    assert "paid" in guide.lower()
