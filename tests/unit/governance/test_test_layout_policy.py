from __future__ import annotations

from scripts.ci.validate_test_layout import (
    ChangedPath,
    changed_targets,
    layout_violations,
)


def test_changed_targets_selects_only_added_copied_and_renamed_destinations() -> None:
    changes = changed_targets(
        "\n".join(
            (
                "A\ttests/unit/example/test_added.py",
                "M\ttests/test_issue_12_legacy.py",
                "D\ttests/test_deleted.py",
                "R100\ttests/test_issue_3_old.py\ttests/unit/example/test_behavior.py",
                "C100\ttests/source.py\ttests/integration/example/test_copy.py",
            )
        )
    )

    assert changes == (
        ChangedPath("A", "tests/unit/example/test_added.py"),
        ChangedPath("R100", "tests/unit/example/test_behavior.py"),
        ChangedPath("C100", "tests/integration/example/test_copy.py"),
    )


def test_new_root_level_test_is_rejected() -> None:
    violations = layout_violations((ChangedPath("A", "tests/test_new_behavior.py"),))

    assert violations == (
        "tests/test_new_behavior.py: new ordinary tests must live in a canonical suite directory",
    )


def test_new_issue_numbered_test_is_rejected_in_suite_directory() -> None:
    violations = layout_violations(
        (ChangedPath("A", "tests/integration/runtime/test_issue_123_regression.py"),)
    )

    assert violations == (
        "tests/integration/runtime/test_issue_123_regression.py: new test filenames must describe behavior, not an issue number",
    )


def test_issue_number_without_separator_is_also_rejected() -> None:
    violations = layout_violations((ChangedPath("A", "tests/regression/test_issue45_boundary.py"),))

    assert len(violations) == 1
    assert "issue number" in violations[0]


def test_modifying_existing_legacy_test_is_grandfathered() -> None:
    changes = changed_targets("M\ttests/test_issue_12_legacy.py")

    assert changes == ()
    assert layout_violations(changes) == ()


def test_rename_away_from_legacy_name_is_allowed() -> None:
    changes = changed_targets(
        "R100\ttests/test_issue_3_old.py\ttests/unit/example/test_behavior.py"
    )

    assert layout_violations(changes) == ()


def test_rename_to_issue_numbered_name_is_rejected() -> None:
    changes = changed_targets(
        "R100\ttests/unit/example/test_behavior.py\ttests/regression/test_issue_45_behavior.py"
    )

    assert len(layout_violations(changes)) == 1


def test_non_test_python_and_non_python_paths_are_not_rejected() -> None:
    changes = (
        ChangedPath("A", "tests/helpers.py"),
        ChangedPath("A", "tests/fixtures/sample.json"),
        ChangedPath("A", "docs/test_issue_12_reopen.py"),
    )

    assert layout_violations(changes) == ()
