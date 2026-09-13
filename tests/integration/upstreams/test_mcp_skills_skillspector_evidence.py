from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

import pytest

import ai_multi_agent_platform.adapters.skillspector as skillspector
from ai_multi_agent_platform.adapters.mcp_skills import (
    MCP_SKILLS_EXTENSION_ID,
    PINNED_MCP_PROTOCOL_REVISION,
    McpSkillsAdapter,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.skills.models import SkillTrustStatus
from ai_multi_agent_platform.skills.security_evidence import (
    FilesystemRawSecurityReportStore,
    InMemorySecurityEvidenceRepository,
    SecurityEvidenceService,
    SecurityEvidenceStatus,
)

OBSERVED_IMAGE_ID = "sha256:" + "1" * 64


class _McpRpc:
    def __init__(self, entry: dict[str, object], files: dict[str, bytes]) -> None:
        self.entry = entry
        self.files = files

    @property
    def server_identity(self) -> str:
        return "mcp-skills-fixture"

    @property
    def protocol_revision(self) -> str:
        return PINNED_MCP_PROTOCOL_REVISION

    @property
    def server_capabilities(self) -> Mapping[str, object]:
        return {
            "resources": {},
            "extensions": {MCP_SKILLS_EXTENSION_ID: {}},
        }

    def call(self, method: str, params: Mapping[str, JsonValue]) -> dict[str, object]:
        if method != "skills/get":
            raise AssertionError(f"unexpected method: {method}")
        assert params == {"uri": "skill://demo/SKILL.md"}
        return {
            "resultType": "complete",
            "ttlMs": 300_000,
            "cacheScope": "private",
            "skill": self.entry,
        }

    def read_resource(self, uri: str, *, max_bytes: int) -> bytes:
        content = self.files[uri]
        if len(content) > max_bytes:
            raise ContractError(ErrorCode.RESOURCE_EXHAUSTED, "fixture resource too large")
        return content


def _stage_mcp_candidate(tmp_path: Path):
    content = b"---\nname: demo\ndescription: Demo\n---\n# Demo\n"
    uri = "skill://demo/SKILL.md"
    entry: dict[str, object] = {
        "uri": uri,
        "frontmatter": {"name": "demo", "description": "Demo", "license": "MIT"},
        "resources": [
            {
                "uri": uri,
                "digest": f"sha256:{sha256(content).hexdigest()}",
                "size": len(content),
            }
        ],
    }
    adapter = McpSkillsAdapter(_McpRpc(entry, {uri: content}), tmp_path / "mcp-staging")
    return adapter.fetch_and_stage(uri)


def _image_payload() -> str:
    return json.dumps(
        {
            "Id": OBSERVED_IMAGE_ID,
            "Config": {
                "Labels": {
                    skillspector.LABEL_REVISION: skillspector.PINNED_REVISION,
                    skillspector.LABEL_DEPENDENCIES: skillspector.PINNED_DEPENDENCY_SET_SHA256,
                    skillspector.LABEL_MODE: skillspector.SCAN_MODE,
                    skillspector.LABEL_POLICY: skillspector.POLICY_CONFIG_REVISION,
                }
            },
        }
    )


def _report(*, complete: bool = True, status: str = "complete") -> dict[str, object]:
    return {
        "execution_successful": True,
        "skill": {"scanned_at": "2026-09-13T12:00:00+00:00"},
        "analysis_completeness": {
            "is_complete": complete,
            "execution_successful": True,
            "status": status,
        },
        "issues": [],
        "risk_assessment": {
            "risk_score": 0,
            "risk_severity": "LOW",
            "recommendation": "SAFE",
        },
    }


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, object],
) -> None:
    monkeypatch.setattr(skillspector.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(
        command: list[str] | tuple[str, ...],
        *,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        values = list(command)
        if len(values) > 2 and values[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(values, 0, _image_payload(), "")
        output_mount = next(
            values[index + 1]
            for index, value in enumerate(values[:-1])
            if value == "-v" and values[index + 1].endswith(":/out:rw")
        )
        output = Path(output_mount.removesuffix(":/out:rw"))
        (output / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(values, 0, "", "")

    monkeypatch.setattr(skillspector, "_run", fake_run)


def _provider(tmp_path: Path) -> skillspector.SkillSpectorSecurityEvidenceProvider:
    return skillspector.SkillSpectorSecurityEvidenceProvider(
        skillspector.SkillSpectorConfig(enabled=True, runtime="docker"),
        FilesystemRawSecurityReportStore(tmp_path / "raw-reports"),
    )


def test_mcp_snapshot_can_attach_clean_skillspector_evidence_without_trust_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _stage_mcp_candidate(tmp_path)
    _install_fake_runtime(monkeypatch, _report())
    repository = InMemorySecurityEvidenceRepository()

    evidence = SecurityEvidenceService(repository, (_provider(tmp_path),)).scan(
        skillspector.PROVIDER_ID,
        candidate.staged,
    )

    assert evidence.status is SecurityEvidenceStatus.CLEAN
    assert evidence.complete is True
    assert evidence.candidate_id == candidate.staged.candidate_id
    assert evidence.candidate_digest == candidate.staged.candidate_digest
    assert repository.list_for_candidate(candidate.staged.candidate_id, 1) == (evidence,)
    assert candidate.profile.trust_status is SkillTrustStatus.DISCOVERED
    assert candidate.profile.enabled is False


def test_degraded_skillspector_evidence_from_mcp_snapshot_stays_degraded_and_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _stage_mcp_candidate(tmp_path)
    _install_fake_runtime(monkeypatch, _report(complete=False, status="partial"))

    evidence = _provider(tmp_path).scan(candidate.staged)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.complete is False
    assert "provider_analysis_incomplete" in evidence.degraded_reasons
    assert "provider_analysis_status=partial" in evidence.degraded_reasons
    assert candidate.profile.trust_status is SkillTrustStatus.DISCOVERED
    assert candidate.profile.enabled is False
