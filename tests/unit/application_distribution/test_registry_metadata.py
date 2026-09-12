from pathlib import Path

from ai_multi_agent_platform.distribution import FilesystemRegistryProvider

CATALOG = Path(__file__).parents[3] / "catalogs" / "technical-components" / "catalog.json"


def test_registry_metadata_tracks_current_candidate_audit() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    projectatlas = provider.get("projectatlas")
    assert projectatlas.version == "0.4.5"
    assert projectatlas.publisher == "styler-ai"
    assert projectatlas.requested_permissions == frozenset(
        {"capability_registration", "worker_execution"}
    )
    assert {
        "functional-pilot-passed",
        "checksum-verified",
        "egress-unverified",
        "health-index-status-only",
    }.issubset(projectatlas.tags)
    assert projectatlas.review_reference is not None
    assert projectatlas.review_reference.endswith("/issues/502")
    assert projectatlas.trust_status.value == "untrusted"

    graphify = provider.get("graphify")
    assert graphify.version == "0.9.56"
    assert graphify.publisher == "Graphify Labs"
    assert graphify.review_reference is not None
    assert graphify.review_reference.endswith("/issues/502")

    codegraph = provider.get("codegraph")
    assert codegraph.version == "0.20.1"
    assert codegraph.review_reference is not None
    assert codegraph.review_reference.endswith("/issues/502")

    understand_anything = provider.get("understand-anything")
    assert understand_anything.version == "2.9.0"
    assert understand_anything.publisher == "Egonex-AI"
    assert understand_anything.source.repository == (
        "https://github.com/Egonex-AI/Understand-Anything"
    )
    assert understand_anything.review_reference is not None
    assert understand_anything.review_reference.endswith("/issues/502")
