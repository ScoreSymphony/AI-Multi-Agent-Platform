from ai_multi_agent_platform.release import (
    UpdateClassification,
    discover_git_heads,
    load_compatibility_inventory,
)

NOW = "2026-09-06T18:40:00Z"


def _remote_revision(pinned_revision: str) -> str:
    return pinned_revision.rsplit("/", 1)[-1].strip()


def test_git_discovery_is_provider_neutral_and_forces_unknown_review_for_changed_head() -> None:
    inventory = load_compatibility_inventory()
    current = inventory.entries[0]
    revisions = {entry.source_url: _remote_revision(entry.revision) for entry in inventory.entries}
    revisions[current.source_url] = "d" * 40

    result = discover_git_heads(
        inventory,
        observed_at=NOW,
        resolver=lambda url: revisions[url],
    )
    candidate = next(item for item in result.observations if item.component == current.component)

    assert candidate.revision == "d" * 40
    assert candidate.classifications == (UpdateClassification.UNKNOWN,)
    assert candidate.license == current.license
    assert result.errors == {}


def test_git_discovery_recognizes_composite_pin_containing_same_commit() -> None:
    inventory = load_compatibility_inventory()
    composite = next(entry for entry in inventory.entries if "/" in entry.revision)
    revisions = {entry.source_url: _remote_revision(entry.revision) for entry in inventory.entries}

    result = discover_git_heads(
        inventory,
        observed_at=NOW,
        resolver=lambda url: revisions[url],
    )
    observed = next(item for item in result.observations if item.component == composite.component)

    assert observed.revision == composite.revision
    assert observed.classifications == ()
