from __future__ import annotations

import hashlib

from ai_multi_agent_platform.distributed.workspace_transport_codec import (
    _entry_token,
    _ManifestEntry,
)


def _entry(relative_path: str) -> _ManifestEntry:
    return _ManifestEntry(
        relative_path=relative_path,
        file_id="file-test",
        sha256=hashlib.sha256(b"payload").hexdigest(),
        size_bytes=7,
    )


def test_worker_local_entry_token_preserves_windows_path_budget() -> None:
    entry = _entry("src/deeply/nested/input.txt")

    token = _entry_token(entry)

    assert token == hashlib.sha256(entry.relative_path.encode("utf-8")).hexdigest()[:32]
    assert len(token) == 32
    assert "/" not in token
    assert "\\" not in token


def test_worker_local_entry_token_remains_path_sensitive() -> None:
    assert _entry_token(_entry("src/a.txt")) != _entry_token(_entry("src/b.txt"))
