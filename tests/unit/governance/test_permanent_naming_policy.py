from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.validate_permanent_naming import (  # noqa: E402
    ChangedPath,
    changed_targets,
    path_violations,
    source_violations,
)


def test_changed_targets_include_added_modified_copied_and_renamed_destinations() -> None:
    changes = changed_targets(
        "\n".join(
            (
                "A\tsrc/package/new.py",
                "M\ttests/unit/test_behavior.py",
                "D\tscripts/ci/old.py",
                "R100\tscripts/ci/old.py\tscripts/ci/new.py",
                "C100\ttests/source.py\ttests/integration/test_copy.py",
            )
        )
    )

    assert changes == (
        ChangedPath("A", "src/package/new.py"),
        ChangedPath("M", "tests/unit/test_behavior.py"),
        ChangedPath("R100", "scripts/ci/new.py"),
        ChangedPath("C100", "tests/integration/test_copy.py"),
    )


def test_issue_numbered_permanent_path_is_rejected() -> None:
    violations = path_violations((ChangedPath("A", "scripts/ci/issue123_release_gate.py"),))

    assert violations == (
        "scripts/ci/issue123_release_gate.py: permanent paths must describe behavior, "
        "not a GitHub issue number",
    )


def test_evidence_path_may_keep_issue_provenance() -> None:
    changes = (ChangedPath("A", "tests/evidence/issue_123/report.py"),)

    assert path_violations(changes) == ()
    assert source_violations(changes[0].path, "def test_issue_123_evidence():\n    pass\n") == ()


def test_behavior_oriented_python_identifiers_are_allowed() -> None:
    source = """
def test_recovery_completion_is_idempotent() -> None:
    recovery_attempts = 2
    assert recovery_attempts == 2
"""

    assert source_violations("tests/unit/test_recovery.py", source) == ()


def test_issue_numbered_test_and_helper_identifiers_are_rejected() -> None:
    source = """
def _issue_20_manifest_document() -> dict[str, object]:
    return {}


def test_issue_20_manifest_contract() -> None:
    assert _issue_20_manifest_document() == {}
"""

    violations = source_violations("tests/unit/plugins/test_plugins.py", source)

    assert any("_issue_20_manifest_document" in violation for violation in violations)
    assert any("test_issue_20_manifest_contract" in violation for violation in violations)


def test_issue_reference_must_be_secondary_provenance_in_comment() -> None:
    bad = "# Issue #439 final hardening\ndef behavior() -> None:\n    pass\n"
    good = (
        "# Prevent duplicate completion events after recovery.\n"
        "# Historical context: issue #439.\n"
        "def behavior() -> None:\n"
        "    pass\n"
    )

    assert len(source_violations("src/package/runtime.py", bad)) == 1
    assert source_violations("src/package/runtime.py", good) == ()


def test_issue_reference_must_be_secondary_provenance_in_docstring() -> None:
    bad = '"""Issue #723 decomposed this compatibility facade."""\n'
    good = (
        '"""Compatibility facade for focused provider modules.\n\n'
        "Historical context: issue #723.\n"
        '"""\n'
    )

    assert len(source_violations("src/package/reference.py", bad)) == 1
    assert source_violations("src/package/reference.py", good) == ()


def test_runtime_strings_with_domain_issue_numbers_are_not_mistaken_for_provenance() -> None:
    source = 'message = "Repository issue #42 is open"\n'

    assert source_violations("src/package/repository_connector.py", source) == ()
