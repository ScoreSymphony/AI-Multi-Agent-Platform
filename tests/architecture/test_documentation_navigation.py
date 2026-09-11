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


def test_status_document_declares_non_competing_ownership() -> None:
    status = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    assert "GitHub issues and their explicit dependencies are authoritative" in status
    assert "IMPLEMENTATION_ROADMAP.md" in status
    assert "[`RELEASE_PROCESS.md`](RELEASE_PROCESS.md)" in status
    assert "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases" in status
    assert "Historical README status snapshot — 2026-09-07" in status


def test_roadmap_points_to_status_without_claiming_live_authority() -> None:
    roadmap = (ROOT / "docs" / "IMPLEMENTATION_ROADMAP.md").read_text(encoding="utf-8")
    assert "[`STATUS.md`](STATUS.md)" in roadmap
    assert "it is not the live work-item tracker" in roadmap
