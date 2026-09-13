from ai_multi_agent_platform.templates.versioning import (
    any_version_satisfies,
    version_satisfies,
)


def test_numeric_version_constraints_support_exact_and_ranges() -> None:
    assert version_satisfies("1", "1")
    assert version_satisfies("1.0", "1")
    assert version_satisfies("1.4.2", ">=1,<2")
    assert version_satisfies("2.0", ">=2,<=2.0.0")
    assert version_satisfies("2.1", "!=2.0")

    assert not version_satisfies("2.0", ">=1,<2")
    assert not version_satisfies("1.9", ">=2")
    assert not version_satisfies("1.0", "!=1")


def test_non_numeric_labels_are_exact_only_and_ordering_fails_closed() -> None:
    assert version_satisfies("vNext", "vNext")
    assert not version_satisfies("vNext", "vPrevious")
    assert not version_satisfies("vNext", ">=1")
    assert not version_satisfies("1.0", ">=broken")
    assert not version_satisfies("1.0", ">=1,")


def test_any_version_satisfies_uses_actual_canonical_inventory() -> None:
    assert any_version_satisfies(("1.0", "1.5", "2.0"), ">=1.2,<2")
    assert not any_version_satisfies(("1.0", "2.0"), ">1,<2")
    assert not any_version_satisfies((), ">=1")
