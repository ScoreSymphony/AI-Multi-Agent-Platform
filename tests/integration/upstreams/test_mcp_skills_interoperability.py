from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.adapters.mcp_skills import (
    MCP_SKILLS_EXTENSION_ID,
    PINNED_EXT_SKILLS_REVISION,
    PINNED_MCP_PROTOCOL_REVISION,
    PINNED_SEP_2640_REVISION,
    McpSkillsAdapter,
    McpSkillsCompatibilityStatus,
    McpSkillsLimits,
)
from ai_multi_agent_platform.adapters.skillspector import digest_tree
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.skills.models import SkillEvaluationStatus, SkillTrustStatus
from ai_multi_agent_platform.skills.repository import InMemorySkillRepository
from ai_multi_agent_platform.skills.resolver import SkillResolutionRequest, SkillResolver
from ai_multi_agent_platform.skills.service import SkillService


class _FixtureRpc:
    def __init__(
        self,
        *,
        entry: dict[str, object] | None = None,
        files: dict[str, bytes] | None = None,
        protocol_revision: str = PINNED_MCP_PROTOCOL_REVISION,
        extension: object = None,
        server_identity: str = "fixture-server",
    ) -> None:
        self._entry = entry
        self._files = files or {}
        self._protocol_revision = protocol_revision
        self._server_identity = server_identity
        if extension is None:
            extension = {}
        self._capabilities: dict[str, object] = {
            "resources": {},
            "extensions": {MCP_SKILLS_EXTENSION_ID: extension},
        }
        self.list_payload: dict[str, object] | None = None
        self.get_payload: dict[str, object] | None = None
        self.read_limits: list[tuple[str, int]] = []

    @property
    def server_identity(self) -> str:
        return self._server_identity

    @property
    def protocol_revision(self) -> str:
        return self._protocol_revision

    @property
    def server_capabilities(self) -> dict[str, object]:
        return self._capabilities

    @staticmethod
    def _cacheable_result(**payload: object) -> dict[str, object]:
        return {
            "resultType": "complete",
            "ttlMs": 300_000,
            "cacheScope": "private",
            **payload,
        }

    def call(self, method: str, params: Mapping[str, JsonValue]) -> dict[str, object]:
        del params
        if method == "skills/list":
            if self.list_payload is not None:
                return self.list_payload
            return self._cacheable_result(skills=[] if self._entry is None else [self._entry])
        if method == "skills/get":
            if self.get_payload is not None:
                return self.get_payload
            return self._cacheable_result(skill=self._entry)
        raise AssertionError(f"unexpected method: {method}")

    def read_resource(self, uri: str, *, max_bytes: int) -> bytes:
        self.read_limits.append((uri, max_bytes))
        try:
            content = self._files[uri]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"fixture resource unavailable: {uri}",
            ) from exc
        if len(content) > max_bytes:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                f"fixture resource exceeded bounded read: {uri}",
            )
        return content


def _payload(
    *,
    skill_name: str = "demo",
    skill_md: bytes = b"---\nname: demo\ndescription: Demo\n---\n# Demo\n",
    supporting: dict[str, bytes] | None = None,
    frontmatter: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, bytes]]:
    files = {"SKILL.md": skill_md, **(supporting or {})}
    base = f"skill://{skill_name}"
    remote_files = {f"{base}/{path}": content for path, content in files.items()}
    resources = [
        {
            "uri": uri,
            "digest": f"sha256:{sha256(content).hexdigest()}",
            "size": len(content),
        }
        for uri, content in remote_files.items()
    ]
    return (
        {
            "uri": f"{base}/SKILL.md",
            "frontmatter": frontmatter
            or {
                "name": skill_name,
                "description": "Demo",
                "license": "MIT",
            },
            "resources": resources,
        },
        remote_files,
    )


def _adapter(tmp_path: Path, **kwargs: Any) -> tuple[McpSkillsAdapter, _FixtureRpc]:
    entry, files = _payload()
    rpc = _FixtureRpc(entry=entry, files=files, **kwargs)
    return McpSkillsAdapter(rpc, tmp_path / "staging"), rpc


def test_revision_pins_are_explicit() -> None:
    assert PINNED_MCP_PROTOCOL_REVISION == "2026-07-28"
    assert PINNED_SEP_2640_REVISION == "582d814a2fb1607aa46045d0cc7f14e0f4da9945"
    assert PINNED_EXT_SKILLS_REVISION == "d866efdba298b55b8156c7b7aa1bdebc1b625f4c"


def test_capability_negotiation_accepts_only_pinned_shape(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path, extension={"directoryRead": True})
    compatibility = adapter.detect()

    assert compatibility.status is McpSkillsCompatibilityStatus.SUPPORTED
    assert compatibility.directory_read is True


def test_server_without_skills_is_explicitly_unsupported(tmp_path: Path) -> None:
    adapter, rpc = _adapter(tmp_path)
    rpc._capabilities["extensions"] = {}

    compatibility = adapter.detect()
    assert compatibility.status is McpSkillsCompatibilityStatus.UNSUPPORTED

    with pytest.raises(ContractError) as raised:
        adapter.discover()
    assert raised.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_mismatched_protocol_revision_fails_closed(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path, protocol_revision="2026-09-01")

    assert adapter.detect().status is McpSkillsCompatibilityStatus.INCOMPATIBLE
    with pytest.raises(ContractError) as raised:
        adapter.get("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_unknown_extension_setting_fails_closed(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path, extension={"futureMode": True})
    assert adapter.detect().status is McpSkillsCompatibilityStatus.INCOMPATIBLE


def test_discovery_and_get_use_current_list_get_shape(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)

    discovered = adapter.discover()
    fetched = adapter.get("skill://demo/SKILL.md")

    assert discovered == (fetched,)
    assert fetched.uri == "skill://demo/SKILL.md"


@pytest.mark.parametrize(
    "payload",
    [
        {"skills": [], "ttlMs": 300_000, "cacheScope": "private"},
        {
            "resultType": "partial",
            "skills": [],
            "ttlMs": 300_000,
            "cacheScope": "private",
        },
        {"resultType": "complete", "skills": [], "cacheScope": "private"},
        {
            "resultType": "complete",
            "skills": [],
            "ttlMs": -1,
            "cacheScope": "private",
        },
        {
            "resultType": "complete",
            "skills": [],
            "ttlMs": True,
            "cacheScope": "private",
        },
        {"resultType": "complete", "skills": [], "ttlMs": 300_000},
        {
            "resultType": "complete",
            "skills": [],
            "ttlMs": 300_000,
            "cacheScope": "shared",
        },
    ],
)
def test_list_result_requires_exact_pinned_cacheable_envelope(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    adapter, rpc = _adapter(tmp_path)
    rpc.list_payload = payload

    with pytest.raises(ContractError) as raised:
        adapter.discover()
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


@pytest.mark.parametrize(
    "payload",
    [
        {"skill": None, "ttlMs": 300_000, "cacheScope": "private"},
        {
            "resultType": "complete",
            "skill": None,
            "ttlMs": 300_000,
            "cacheScope": "shared",
        },
        {
            "resultType": "complete",
            "skill": None,
            "ttlMs": -1,
            "cacheScope": "private",
        },
    ],
)
def test_get_result_requires_exact_pinned_cacheable_envelope(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    adapter, rpc = _adapter(tmp_path)
    rpc.get_payload = payload

    with pytest.raises(ContractError) as raised:
        adapter.get("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_malformed_partial_entry_is_rejected(tmp_path: Path) -> None:
    adapter, rpc = _adapter(tmp_path)
    rpc._entry = {
        "uri": "skill://demo/SKILL.md",
        "frontmatter": {"name": "demo", "description": "Demo"},
    }

    with pytest.raises(ContractError) as raised:
        adapter.get("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_dynamic_skill_is_discoverable_but_not_canonicalizable(tmp_path: Path) -> None:
    adapter, rpc = _adapter(tmp_path)
    assert rpc._entry is not None
    rpc._entry["resources"] = "dynamic"

    assert adapter.discover()[0].resources == "dynamic"
    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.CONTRACT_VIOLATION


def test_staging_verifies_digests_and_is_skillspector_compatible(tmp_path: Path) -> None:
    entry, files = _payload(supporting={"references/notes.md": b"safe notes\n"})
    rpc = _FixtureRpc(entry=entry, files=files)
    candidate = McpSkillsAdapter(rpc, tmp_path / "staging").fetch_and_stage(
        "skill://demo/SKILL.md"
    )

    assert candidate.staged.snapshot_path.joinpath("SKILL.md").exists()
    assert candidate.staged.snapshot_path.joinpath("references", "notes.md").exists()
    assert digest_tree(candidate.staged.snapshot_path) == candidate.staged.candidate_digest
    assert candidate.profile.trust_status is SkillTrustStatus.DISCOVERED
    assert candidate.profile.enabled is False


def test_read_is_bounded_by_declared_size_before_body_is_returned(tmp_path: Path) -> None:
    entry, files = _payload()
    resources = entry["resources"]
    assert isinstance(resources, list)
    first = resources[0]
    assert isinstance(first, dict)
    first["size"] = 1
    rpc = _FixtureRpc(entry=entry, files=files)
    adapter = McpSkillsAdapter(rpc, tmp_path / "staging")

    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")

    assert raised.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert rpc.read_limits[0] == ("skill://demo/SKILL.md", 1)


def test_digest_mismatch_fails_before_canonical_intake(tmp_path: Path) -> None:
    entry, files = _payload()
    files["skill://demo/SKILL.md"] = b"tampered"
    adapter = McpSkillsAdapter(_FixtureRpc(entry=entry, files=files), tmp_path / "staging")

    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE
    assert list((tmp_path / "staging").glob("[!.]*")) == []


@pytest.mark.parametrize(
    "unsafe_uri",
    [
        "skill://demo/%2e%2e/escape.txt",
        "skill://demo/%2E%2E/escape.txt",
        "skill://demo/a%2fb.txt",
        "skill://demo/a%5cb.txt",
    ],
)
def test_unsafe_paths_are_rejected(tmp_path: Path, unsafe_uri: str) -> None:
    entry, files = _payload()
    content = b"escape"
    resources = entry["resources"]
    assert isinstance(resources, list)
    resources.append(
        {
            "uri": unsafe_uri,
            "digest": f"sha256:{sha256(content).hexdigest()}",
            "size": len(content),
        }
    )
    files[unsafe_uri] = content
    adapter = McpSkillsAdapter(_FixtureRpc(entry=entry, files=files), tmp_path / "staging")

    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_case_colliding_paths_are_rejected_for_portability(tmp_path: Path) -> None:
    entry, files = _payload(supporting={"Readme.md": b"a", "README.md": b"b"})
    adapter = McpSkillsAdapter(_FixtureRpc(entry=entry, files=files), tmp_path / "staging")

    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


def test_staging_is_bounded(tmp_path: Path) -> None:
    entry, files = _payload(supporting={"large.bin": b"x" * 20})
    adapter = McpSkillsAdapter(
        _FixtureRpc(entry=entry, files=files),
        tmp_path / "staging",
        limits=McpSkillsLimits(max_files=10, max_file_bytes=10, max_total_bytes=100),
    )

    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.RESOURCE_EXHAUSTED


def test_identical_content_maps_deterministically(tmp_path: Path) -> None:
    entry, files = _payload()
    adapter = McpSkillsAdapter(_FixtureRpc(entry=entry, files=files), tmp_path / "staging")

    first = adapter.fetch_and_stage("skill://demo/SKILL.md")
    second = adapter.fetch_and_stage("skill://demo/SKILL.md")

    assert first.staged.candidate_id == second.staged.candidate_id
    assert first.staged.candidate_digest == second.staged.candidate_digest
    assert first.manifest_digest == second.manifest_digest
    assert first.staged.snapshot_path == second.staged.snapshot_path


def test_changed_remote_content_creates_new_candidate_and_preserves_old_snapshot(
    tmp_path: Path,
) -> None:
    first_entry, first_files = _payload()
    root = tmp_path / "staging"
    first = McpSkillsAdapter(
        _FixtureRpc(entry=first_entry, files=first_files), root
    ).fetch_and_stage("skill://demo/SKILL.md")
    old_bytes = first.staged.snapshot_path.joinpath("SKILL.md").read_bytes()

    second_entry, second_files = _payload(
        skill_md=b"---\nname: demo\ndescription: Demo changed\n---\n# Demo v2\n",
        frontmatter={"name": "demo", "description": "Demo changed", "license": "MIT"},
    )
    second = McpSkillsAdapter(
        _FixtureRpc(entry=second_entry, files=second_files),
        root,
    ).fetch_and_stage("skill://demo/SKILL.md")

    assert second.staged.candidate_id != first.staged.candidate_id
    assert second.staged.candidate_digest != first.staged.candidate_digest
    assert first.staged.snapshot_path.joinpath("SKILL.md").read_bytes() == old_bytes
    assert first.staged.snapshot_path.exists()


def test_remote_capability_declarations_are_evidence_not_grants(tmp_path: Path) -> None:
    entry, files = _payload(
        frontmatter={
            "name": "demo",
            "description": "Demo",
            "allowed-tools": ["filesystem.write", "shell.exec"],
        }
    )
    candidate = McpSkillsAdapter(
        _FixtureRpc(entry=entry, files=files),
        tmp_path / "staging",
    ).fetch_and_stage("skill://demo/SKILL.md")

    assert candidate.profile.capability_requirements == ()
    assert candidate.profile.source is not None
    assert candidate.profile.source.requested_capability_ids == ()
    assert candidate.profile.metadata["remote_declarations_are_review_inputs_only"] is True


def test_mcp_origin_candidate_enters_normal_untrusted_skill_lifecycle(tmp_path: Path) -> None:
    adapter, _ = _adapter(tmp_path)
    candidate = adapter.fetch_and_stage("skill://demo/SKILL.md")
    repository = InMemorySkillRepository()
    service = SkillService(repository)

    revision = service.create_skill(
        candidate.profile,
        owner_ref=OwnerRef(type="service", id="mcp-skills-test"),
        provenance=candidate.provenance,
        skill_id=candidate.staged.candidate_id,
    )

    assert revision.profile.trust_status is SkillTrustStatus.DISCOVERED
    assert revision.profile.enabled is False
    with pytest.raises(ContractError) as raised:
        service.set_enabled(revision.skill_id, True, expected_revision=revision.revision)
    assert raised.value.code is ErrorCode.FORBIDDEN


def test_adopted_candidate_uses_ordinary_bundle_and_remote_drift_cannot_rewrite_it(
    tmp_path: Path,
) -> None:
    entry, files = _payload()
    root = tmp_path / "staging"
    candidate = McpSkillsAdapter(_FixtureRpc(entry=entry, files=files), root).fetch_and_stage(
        "skill://demo/SKILL.md"
    )
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    revision = service.create_skill(
        candidate.profile,
        owner_ref=OwnerRef(type="service", id="mcp-skills-test"),
        provenance=candidate.provenance,
        skill_id=candidate.staged.candidate_id,
    )
    for status in (
        SkillTrustStatus.SOURCE_VERIFIED,
        SkillTrustStatus.SECURITY_REVIEWED,
        SkillTrustStatus.PILOT,
    ):
        revision = service.transition_trust(
            revision.skill_id,
            status,
            expected_revision=revision.revision,
        )
    revision = service.transition_trust(
        revision.skill_id,
        SkillTrustStatus.ADOPTED,
        expected_revision=revision.revision,
        evaluation_status=SkillEvaluationStatus.PASSED,
    )
    revision = service.set_enabled(
        revision.skill_id,
        True,
        expected_revision=revision.revision,
    )

    request = SkillResolutionRequest(
        run_id=new_id("run"),
        task_id=new_id("task"),
        agent_id=new_id("agent"),
        agent_revision=1,
        agent_role="worker",
        explicit_skills=(revision.ref,),
    )
    resolver = SkillResolver(repository)
    first_bundle = resolver.resolve(request)

    changed_entry, changed_files = _payload(
        skill_md=b"---\nname: demo\ndescription: changed\n---\n# changed\n",
        frontmatter={"name": "demo", "description": "changed"},
    )
    changed = McpSkillsAdapter(
        _FixtureRpc(entry=changed_entry, files=changed_files),
        root,
    ).fetch_and_stage("skill://demo/SKILL.md")
    second_bundle = resolver.resolve(request)

    assert changed.staged.candidate_id != revision.skill_id
    assert repository.get_skill_revision(revision.skill_id, revision.revision) == revision
    assert first_bundle.digest == second_bundle.digest
    assert first_bundle.entries == second_bundle.entries
    assert first_bundle.entries[0].ref == revision.ref


def test_partial_file_retrieval_failure_is_explicit(tmp_path: Path) -> None:
    entry, files = _payload(supporting={"notes.md": b"notes"})
    del files["skill://demo/notes.md"]
    adapter = McpSkillsAdapter(_FixtureRpc(entry=entry, files=files), tmp_path / "staging")

    with pytest.raises(ContractError) as raised:
        adapter.fetch_and_stage("skill://demo/SKILL.md")
    assert raised.value.code is ErrorCode.UNAVAILABLE
