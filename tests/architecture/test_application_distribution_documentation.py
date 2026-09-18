from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION_DOC = ROOT / "docs" / "product" / "APPLICATION_DISTRIBUTION.md"
CONFORMANCE_DOC = ROOT / "docs" / "product" / "APPLICATION_DISTRIBUTION_CONFORMANCE.md"

SUPERSEDED_REMOTE_LIMITATIONS = (
    "does not yet dispatch the command to a remote Worker",
    "Distributed inventory therefore must not be used to claim that a target is buildable "
    "until remote build execution",
    "Remote Worker build dispatch can replace this path later",
)

CURRENT_BEHAVIOR_MARKERS = (
    "DistributedApplicationBuildLifecycleBackend",
    "DistributedBuildTargetMatcher",
    "DistributedRuntime",
    "scoped Worker secret delivery",
    "#748",
    "#749",
    "#750",
    "#751",
    "APPLICATION_DISTRIBUTION_CONFORMANCE.md",
)

MAINTAINED_EVIDENCE = (
    "tests/integration/application_distribution/test_remote_worker_multitarget.py",
    "tests/integration/application_distribution/test_remote_worker_result_evidence.py",
    "tests/integration/application_distribution/test_remote_worker_recovery.py",
    "tests/integration/application_distribution/test_remote_worker_lost_replies.py",
    "tests/integration/application_distribution/test_remote_build_security.py",
    "tests/integration/application_distribution/test_build_secret_environment.py",
    "tests/integration/application_distribution/test_build_secret_environment_fail_closed.py",
    "tests/integration/application_distribution/test_release_gate_provenance.py",
    "tests/integration/application_distribution/test_release_gate_verification_restart.py",
    "tests/integration/application_distribution/test_release_gate_evaluation_orchestration.py",
    "tests/integration/application_distribution/test_release_gate_package_smoke.py",
    "tests/integration/application_distribution/test_github_conformance.py",
)


def test_application_distribution_doc_does_not_restore_superseded_remote_dispatch_limitations(\n) -> None:
    documentation = DISTRIBUTION_DOC.read_text(encoding="utf-8")

    for stale in SUPERSEDED_REMOTE_LIMITATIONS:
        assert stale not in documentation

    for marker in CURRENT_BEHAVIOR_MARKERS:
        assert marker in documentation


def test_application_distribution_conformance_evidence_is_current_and_resolves() -> None:
    conformance = CONFORMANCE_DOC.read_text(encoding="utf-8")

    for evidence in MAINTAINED_EVIDENCE:
        assert evidence in conformance
        assert (ROOT / evidence).is_file(), f"missing maintained evidence: {evidence}"

    assert "fails before Worker dispatch until scoped Worker secret delivery exists" in conformance
