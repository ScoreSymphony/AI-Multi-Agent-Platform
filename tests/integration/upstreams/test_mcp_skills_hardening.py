from __future__ import annotations

import os
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

import ai_multi_agent_platform.adapters.mcp_skills as mcp_skills
from ai_multi_agent_platform.adapters.mcp_skills import (
    MCP_SKILLS_EXTENSION_ID,
    PINNED_MCP_PROTOCOL_REVISION,
    McpSkillCanonicalCandidate,
    McpSkillsAdapter,
)
from ai_multi_agent_platform.contracts.types import JsonValue


class _Rpc:
    def __init__(
        self,
        *,
        entry: dict[str, object],
        files: dict[str, bytes],
    ) -> None:
        self.entry = entry
        self.files = files

    @property
    def server_identity(self) -> str:
        return "hardening-fixture"

    @property
    def protocol_revision(self) -> str:
        return PINNED_MCP_PROTOCOL_REVISION

    @property
    def server_capabilities(self) -> Mapping[str, object]:
        return {
            "resources": {},
            "extensions": {MCP_SKILLS_EXTENSION_ID: {}},
        }

    def call(self, method: str, params: Mapping[str, JsonValue]) -> Mapping[str, object]:
        del params
        if method != "skills/get":
            raise AssertionError(f"unexpected method: {method}")
        return {
            "resultType": "complete",
            "ttlMs": 0,
            "cacheScope": "private",
            "skill": self.entry,
        }

    def read_resource(self, uri: str, *, max_bytes: int) -> bytes:
        content = self.files[uri]
        if len(content) > max_bytes:
            raise AssertionError("fixture violated bounded-read contract")
        return content


def _payload(
    *,
    frontmatter: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, bytes]]:
    content = b"---\nname: demo\ndescription: Demo\n---\n# Demo\n"
    uri = "skill://demo/SKILL.md"
    return (
        {
            "uri": uri,
            "frontmatter": frontmatter or {"name": "demo", "description": "Demo", "license": "MIT"},
            "resources": [
                {
                    "uri": uri,
                    "digest": f"sha256:{sha256(content).hexdigest()}",
                    "size": len(content),
                }
            ],
        },
        {uri: content},
    )


def _stage(
    tmp_path: Path,
    *,
    frontmatter: dict[str, object] | None = None,
) -> McpSkillCanonicalCandidate:
    entry, files = _payload(frontmatter=frontmatter)
    return McpSkillsAdapter(_Rpc(entry=entry, files=files), tmp_path / "staging").fetch_and_stage(
        "skill://demo/SKILL.md"
    )


def test_snapshot_digest_framing_distinguishes_embedded_nul_boundaries(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first.joinpath("a").write_bytes(b"x\0b\0y")
    second.joinpath("a").write_bytes(b"x")
    second.joinpath("b").write_bytes(b"y")

    assert mcp_skills._digest_tree(first) != mcp_skills._digest_tree(second)


def test_frontmatter_evidence_is_defensively_deep_frozen(tmp_path: Path) -> None:
    nested_scope = ["workspace"]
    allowed_tools: list[object] = ["filesystem.read", {"scope": nested_scope}]
    frontmatter: dict[str, object] = {
        "name": "demo",
        "description": "Demo",
        "allowed-tools": allowed_tools,
    }

    candidate = _stage(tmp_path, frontmatter=frontmatter)
    allowed_tools.append("shell.exec")
    nested_scope.append("host")

    remote_allowed = cast(tuple[object, ...], candidate.remote_entry.frontmatter["allowed-tools"])
    assert len(remote_allowed) == 2
    nested = cast(Mapping[str, object], remote_allowed[1])
    assert nested["scope"] == ("workspace",)

    profile_frontmatter = cast(
        Mapping[str, object], candidate.profile.metadata["frontmatter_evidence"]
    )
    assert profile_frontmatter["allowed-tools"] == remote_allowed

    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["scope"] = ("mutated",)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission hardening")
def test_snapshot_is_read_only_before_atomic_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, files = _payload()
    adapter = McpSkillsAdapter(_Rpc(entry=entry, files=files), tmp_path / "staging")
    real_replace = mcp_skills.os.replace
    observed = False

    def guarded_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal observed
        source_path = Path(source)
        assert source_path.stat().st_mode & 0o222 == 0
        assert all(path.stat().st_mode & 0o222 == 0 for path in source_path.rglob("*"))
        observed = True
        real_replace(source, destination)

    monkeypatch.setattr(mcp_skills.os, "replace", guarded_replace)

    adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert observed is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission hardening")
def test_existing_snapshot_is_rehardened_before_reuse(tmp_path: Path) -> None:
    entry, files = _payload()
    adapter = McpSkillsAdapter(_Rpc(entry=entry, files=files), tmp_path / "staging")
    first = adapter.fetch_and_stage("skill://demo/SKILL.md")
    root = first.staged.snapshot_path
    skill_md = root / "SKILL.md"
    root.chmod(0o755)
    skill_md.chmod(0o644)

    second = adapter.fetch_and_stage("skill://demo/SKILL.md")

    assert second.staged.snapshot_path == root
    assert root.stat().st_mode & 0o222 == 0
    assert skill_md.stat().st_mode & 0o222 == 0
