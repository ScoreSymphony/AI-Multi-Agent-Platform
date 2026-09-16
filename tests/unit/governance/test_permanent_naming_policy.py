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
    workflow_violations,
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
    changes = (
        ChangedPath("A", "scripts/ci/issue123_release_gate.py"),
        ChangedPath("A", "src/package/issue_123/__init__.py"),
        ChangedPath("A", ".github/workflows/issue-123-release.yml"),
    )

    assert path_violations(changes) == (
        "scripts/ci/issue123_release_gate.py: permanent paths must describe behavior, "
        "not a GitHub issue number",
        "src/package/issue_123/__init__.py: permanent paths must describe behavior, "
        "not a GitHub issue number",
        ".github/workflows/issue-123-release.yml: permanent paths must describe behavior, "
        "not a GitHub issue number",
    )


def test_issue_scoped_evidence_path_may_keep_issue_provenance() -> None:
    changes = (ChangedPath("A", "tests/evidence/issue_123/report.py"),)

    assert path_violations(changes) == ()
    assert source_violations(changes[0].path, "def test_issue_123_evidence():\n    pass\n") == ()


def test_evidence_allowlist_does_not_cover_unscoped_files() -> None:
    changes = (ChangedPath("A", "tests/evidence/issue123_report.py"),)

    assert path_violations(changes) == (
        "tests/evidence/issue123_report.py: permanent paths must describe behavior, "
        "not a GitHub issue number",
    )
    violations = source_violations(
        "tests/evidence/shared.py",
        "def test_issue_123_evidence():\n    pass\n",
    )
    assert any("test_issue_123_evidence" in violation for violation in violations)


def test_behavior_oriented_python_identifiers_are_allowed() -> None:
    source = """
def test_recovery_completion_is_idempotent() -> None:
    recovery_attempts = 2
    assert recovery_attempts == 2
"""

    assert source_violations("tests/unit/test_recovery.py", source) == ()


def test_issue_numbered_test_helper_and_attribute_identifiers_are_rejected() -> None:
    source = """
def _issue_20_manifest_document() -> dict[str, object]:
    return {}


class Cache:
    def remember(self, value: object) -> None:
        self.issue_123_cache = value


def test_issue_20_manifest_contract() -> None:
    assert _issue_20_manifest_document() == {}
"""

    violations = source_violations("tests/unit/plugins/test_plugins.py", source)

    assert any("_issue_20_manifest_document" in violation for violation in violations)
    assert any("issue_123_cache" in violation for violation in violations)
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


def test_compact_issue_numbered_semantic_strings_are_rejected() -> None:
    source = """
provider_id = "issue388-control"
topic = "issue-388.retry"
source_component = "issue_388_tests"
"""

    violations = source_violations("tests/integration/test_transport.py", source)

    assert len(violations) == 3
    assert all("embeds a GitHub issue number" in violation for violation in violations)


def test_internal_issue_reference_in_semantic_message_is_rejected() -> None:
    source = 'message = "optional #388 transport evidence failed"\n'

    violations = source_violations("scripts/acceptance/transport.py", source)

    assert len(violations) == 1
    assert "embeds a GitHub issue number" in violations[0]


def test_documented_legacy_evidence_contract_strings_are_allowed() -> None:
    source = """
transport_schema = "ai-multi-agent-platform/issue-388-two-host-transport/v1"
restart_schema = "ai-multi-agent-platform/issue-388-two-host-restart/v1"
evidence_ref = "evidence:issue388-two-host"
network_schema = "ai-multi-agent-platform/issue-562-network-probe/v1"
phase_schema = "ai-multi-agent-platform/issue-562-platform-phase/v1"
report_schema = "ai-multi-agent-platform/issue-562-two-vps-private-tunnel/v1"
evidence_lineage = "issue562:real-two-vps-private-tunnel-report"
"""

    assert source_violations("scripts/acceptance/evidence_contract.py", source) == ()


def test_legacy_compatibility_allowlist_does_not_cover_arbitrary_issue_strings() -> None:
    source = """
provider_id = "issue388-new-provider"
evidence_ref = "evidence:issue388-unrelated"
schema = "ai-multi-agent-platform/issue-562-unrelated/v1"
"""

    violations = source_violations("scripts/acceptance/new_contract.py", source)

    assert len(violations) == 3


def test_runtime_strings_with_domain_issue_numbers_are_not_mistaken_for_provenance() -> None:
    source = 'message = "Repository issue #42 is open"\n'

    assert source_violations("src/package/repository_connector.py", source) == ()


def test_workflow_job_and_step_names_reject_concrete_issue_numbers() -> None:
    source = """
name: Release validation
jobs:
  issue730-gate:
    name: Stable release gate
    runs-on: ubuntu-latest
    steps:
      - name: Issue #730 final validation
        run: echo ok
"""

    violations = workflow_violations(".github/workflows/release.yml", source)

    assert len(violations) == 2
    assert any("workflow job ids" in violation for violation in violations)
    assert any("workflow and step names" in violation for violation in violations)


def test_workflow_repository_issue_domain_vocabulary_remains_allowed() -> None:
    source = """
name: Repository issue synchronization
jobs:
  sync-issues:
    runs-on: ubuntu-latest
    steps:
      - name: Sync repository issues
        run: echo "${{ github.event.issue.number }}"
"""

    assert workflow_violations(".github/workflows/issues.yml", source) == ()
