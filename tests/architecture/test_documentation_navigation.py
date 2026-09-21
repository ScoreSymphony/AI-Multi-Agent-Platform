from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "STATUS.md",
    ROOT / "docs" / "IMPLEMENTATION_ROADMAP.md",
)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _local_link_targets(document: Path) -> list[Path]:
    targets: list[Path] = []
    for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
        parsed = urlsplit(raw_target.strip())
        if parsed.scheme or parsed.netloc or raw_target.startswith("#"):
            continue
        if not parsed.path:
            continue
        targets.append((document.parent / parsed.path).resolve())
    return targets


def test_landing_and_status_documentation_links_resolve() -> None:
    missing: list[str] = []
    for document in DOCUMENTS:
        assert document.is_file(), f"missing documentation file: {document.relative_to(ROOT)}"
        for target in _local_link_targets(document):
            if not target.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target.relative_to(ROOT)}")
    assert not missing, "broken local documentation links:\n" + "\n".join(missing)


def test_moved_readme_status_anchor_has_no_stale_references() -> None:
    stale: list[str] = []
    documents = [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / ".github").rglob("*.md"),
    ]
    readme = (ROOT / "README.md").resolve()

    for document in documents:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            parsed = urlsplit(raw_target.strip())
            if parsed.scheme or parsed.netloc or parsed.fragment.lower() != "status":
                continue
            target = (
                (document.parent / parsed.path).resolve() if parsed.path else document.resolve()
            )
            if target == readme:
                stale.append(f"{document.relative_to(ROOT)} -> {raw_target}")

    assert not stale, "stale links to the removed README #status anchor:\n" + "\n".join(stale)


def test_readme_does_not_become_a_live_issue_ledger_again() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/STATUS.md" in readme
    assert "docs/RELEASE_PROCESS.md" in readme
    assert "Point-in-time status snapshot" not in readme
    assert "At this snapshot there are" not in readme
    assert "Recent convergence materially moved the frontier forward" not in readme


def test_status_document_declares_maintained_sources_without_tracker_ownership() -> None:
    status = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    assert "without depending on issue-tracker chronology" in status
    assert "[README](../README.md)" in status
    assert "[Release process](RELEASE_PROCESS.md)" in status
    assert "[Platform conformance](PLATFORM_CONFORMANCE.md)" in status
    assert (
        "[`docs/history/issues/STATUS_2026-09-11.md`](history/issues/STATUS_2026-09-11.md)"
        in status
    )
    assert "GitHub issues and their explicit dependencies are authoritative" not in status


def test_status_keeps_issue_and_pr_chronology_historical() -> None:
    status = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    assert "Issue- and pull-request-specific integration chronology is historical provenance" in status
    assert "github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues/" not in status
    assert "github.com/ScoreSymphony/AI-Multi-Agent-Platform/pull/" not in status
    assert "The #728 staging branch is kept synchronized" not in status
    assert "Further integration commits may still arrive before final #728 closure" not in status
    assert "before anything reaches `main`" not in status


def test_roadmap_points_to_maintained_guidance_without_live_tracker_semantics() -> None:
    roadmap = (ROOT / "docs" / "IMPLEMENTATION_ROADMAP.md").read_text(encoding="utf-8")
    assert "is no longer an active product or operator contract" in roadmap
    assert "[Feature classification](FEATURE_CLASSIFICATION.md)" in roadmap
    assert "[Platform conformance](PLATFORM_CONFORMANCE.md)" in roadmap
    assert "[Release process](RELEASE_PROCESS.md)" in roadmap
    assert (
        "[`docs/history/issues/IMPLEMENTATION_ROADMAP_2026-09-07.md`]"
        "(history/issues/IMPLEMENTATION_ROADMAP_2026-09-07.md)"
        in roadmap
    )
    assert "GitHub issues and their explicit dependencies are authoritative" not in roadmap
