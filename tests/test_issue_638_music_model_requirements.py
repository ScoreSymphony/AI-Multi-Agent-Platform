from pathlib import Path

from ai_multi_agent_platform.distribution import FilesystemRegistryProvider

CATALOG = Path(__file__).parents[1] / "catalogs" / "technical-components" / "catalog.json"


def test_legato_declares_gated_vision_encoder_as_required_model() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    legato = provider.get("legato")

    assert legato.required_models == ("meta-llama/Llama-3.2-11B-Vision",)
