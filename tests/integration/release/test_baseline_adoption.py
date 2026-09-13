from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import ai_multi_agent_platform.upgrade.versioning as versioning
from ai_multi_agent_platform.upgrade import JsonVersionStateStore, VersionStateError


def test_untracked_data_root_cannot_be_adopted_by_future_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    baseline = versioning.current_release_versions()
    future = replace(baseline, platform_release="0.0.2")
    monkeypatch.setattr(versioning, "current_release_versions", lambda: future)
    store = JsonVersionStateStore.for_data_dir(data_dir)

    with pytest.raises(VersionStateError, match="baseline release 0.0.1"):
        store.initialize()

    assert not store.exists()
