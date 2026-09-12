from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ai_multi_agent_platform.adapters.skillspector as skillspector
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.skills.security_evidence import (
    FilesystemRawSecurityReportStore,
    InMemorySecurityEvidenceRepository,
    JsonSecurityEvidenceRepository,
    SecurityEvidence,
    SecurityEvidenceService,
    SecurityEvidenceStatus,
    SecurityFinding,
    StagedSkillCandidate,
    new_security_evidence_id,
)
from ai_multi_agent_platform.skills.security_evidence_control_plane import (
    security_evidence_resource,
)


def _candidate(tmp_path: Path) -> StagedSkillCandidate:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "SKILL.md").write_text("# Example\nUse local files only.\n", encoding="utf-8")
    return StagedSkillCandidate(
        candidate_id="skill_external-example",
        candidate_revision=3,
        candidate_digest=skillspector.digest_tree(root),
        snapshot_path=root,
    )


def _report(
    *,
    findings: list[dict[str, object]] | None = None,
    complete: bool = True,
    status: str = "complete",
) -> dict[str, object]:
    return {
        "execution_successful": True,
        "skill": {"scanned_at": "2026-09-12T18:00:00+00:00"},
        "analysis_completeness": {
            "is_complete": complete,
            "execution_successful": True,
            "status": status,
        },
        "issues": findings or [],
        "suppressed": [{"rule_id": "P9", "reason": "reviewed baseline"}],
        "baseline": {"id": "baseline-v1", "accepted_count": 1},
        "risk_assessment": {
            "risk_score": 0 if not findings else 100,
            "risk_severity": "LOW" if not findings else "CRITICAL",
            "recommendation": "SAFE" if not findings else "DO_NOT_INSTALL",
        },
    }


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    report: dict[str, object] | None = None,
    malformed: bytes | None = None,
    returncode: int = 0,
    image_id: str = skillspector.PINNED_IMAGE_ID,
    scan_error: BaseException | None = None,
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
            return subprocess.CompletedProcess(values, 0, f"{image_id}\n", "")
        if scan_error is not None:
            raise scan_error
        output_mount = next(
            values[index + 1]
            for index, value in enumerate(values[:-1])
            if value == "-v" and values[index + 1].endswith(":/out:rw")
        )
        output_dir = Path(output_mount.removesuffix(":/out:rw"))
        if malformed is not None:
            (output_dir / "report.json").write_bytes(malformed)
        elif report is not None:
            (output_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(values, returncode, "", "")

    monkeypatch.setattr(skillspector, "_run", fake_run)


def _provider(tmp_path: Path, *, enabled: bool = True) -> skillspector.SkillSpectorSecurityEvidenceProvider:
    return skillspector.SkillSpectorSecurityEvidenceProvider(
        skillspector.SkillSpectorConfig(enabled=enabled, runtime="docker"),
        FilesystemRawSecurityReportStore(tmp_path / "raw-reports"),
    )


def test_production_pin_rejects_unreviewed_provider_drift() -> None:
    with pytest.raises(ValueError, match="rerun #800 corpus"):
        skillspector.SkillSpectorConfig(provider_revision="deadbeef")
    with pytest.raises(ValueError, match="built-image identity"):
        skillspector.SkillSpectorConfig(provider_build_identity="sha256:changed")
    with pytest.raises(ValueError, match="dependency lock"):
        skillspector.SkillSpectorConfig(dependency_set_digest="0" * 64)
    with pytest.raises(ValueError, match="only static_no_llm_network_none"):
        skillspector.SkillSpectorConfig(scan_mode="llm_assisted")


def test_default_provider_is_disabled_and_absence_does_not_block_skill_lifecycle(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    repository = InMemorySecurityEvidenceRepository()
    service = SecurityEvidenceService(repository, (_provider(tmp_path, enabled=False),))

    with pytest.raises(ContractError) as exc_info:
        service.scan(skillspector.PROVIDER_ID, candidate)

    assert exc_info.value.code is ErrorCode.UNAVAILABLE
    assert repository.list_all() == ()


def test_container_command_is_static_no_llm_network_none_and_resource_bounded(
    tmp_path: Path,
) -> None:
    config = skillspector.SkillSpectorConfig(enabled=True)
    command = skillspector.build_container_command(
        "docker",
        config,
        tmp_path / "candidate",
        tmp_path / "output",
    )

    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=256" in command
    assert "--memory=1g" in command
    assert "--cpus=1.0" in command
    assert "/tmp:rw,noexec,nosuid,size=64m" in command
    assert "--no-llm" in command
    assert f"{tmp_path / 'candidate'}:/scan:ro" in command
    assert "HOME=/tmp" in command


def test_sanitized_environment_never_forwards_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    environment = skillspector.sanitized_environment()

    assert environment["PATH"] == "/usr/bin"
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_API_KEY" not in environment
    assert "NVIDIA_API_KEY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment


def test_clean_scan_is_persisted_as_advisory_evidence_not_trust_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report())
    repository = InMemorySecurityEvidenceRepository()
    service = SecurityEvidenceService(repository, (_provider(tmp_path),))

    evidence = service.scan(skillspector.PROVIDER_ID, candidate)

    assert evidence.status is SecurityEvidenceStatus.CLEAN
    assert evidence.complete is True
    assert evidence.candidate_revision == 3
    assert evidence.candidate_digest == candidate.candidate_digest
    assert evidence.provider_revision == skillspector.PINNED_REVISION
    assert evidence.provider_build_identity == skillspector.PINNED_IMAGE_ID
    assert evidence.dependency_set_digest == skillspector.PINNED_DEPENDENCY_SET_SHA256
    assert evidence.scan_mode == "static_no_llm_network_none"
    assert evidence.raw_report_digest is not None
    assert evidence.raw_report_artifact_ref is not None
    assert evidence.network_usage["container_network"] == "none"
    assert evidence.provider_usage["llm_assisted"] is False
    assert not hasattr(evidence, "trust_status")
    assert not hasattr(evidence, "approved")
    assert repository.list_for_candidate(candidate.candidate_id, 3) == (evidence,)


def test_provider_risk_recommendation_stays_metadata_and_rule_id_is_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    finding = {
        "id": "AST4",
        "finding_id": "finding-run-specific-123",
        "category": "dangerous_code",
        "severity": "CRITICAL",
        "confidence": 0.99,
        "finding": "subprocess execution",
        "location": {"file": "tool.py", "start_line": 7},
    }
    _install_fake_runtime(monkeypatch, report=_report(findings=[finding]), returncode=1)
    service = SecurityEvidenceService(
        InMemorySecurityEvidenceRepository(),
        (_provider(tmp_path),),
    )

    evidence = service.scan(skillspector.PROVIDER_ID, candidate)

    assert evidence.status is SecurityEvidenceStatus.FINDINGS
    assert evidence.complete is True
    assert evidence.findings[0].rule_id == "AST4"
    assert evidence.findings[0].occurrence_id == "finding-run-specific-123"
    assert evidence.findings[0].path == "tool.py"
    assert evidence.findings[0].line == 7
    risk = evidence.provider_metadata["risk_assessment"]
    assert isinstance(risk, dict)
    assert risk["recommendation"] == "DO_NOT_INSTALL"
    assert evidence.provider_metadata["provider_native_recommendation_is_advisory"] is True
    assert not hasattr(evidence, "delete_skill")
    assert not hasattr(evidence, "reject_skill")


def test_suppression_and_baseline_metadata_are_preserved_without_approval_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report())

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.suppression_metadata[0]["rule_id"] == "P9"
    assert evidence.baseline_metadata["id"] == "baseline-v1"
    assert not hasattr(evidence, "approval_id")


def test_offline_supply_chain_partial_result_is_degraded_not_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    partial = _report(
        findings=[{"id": "SC4", "severity": "MEDIUM", "finding": "OSV fallback"}],
        complete=False,
        status="partial",
    )
    _install_fake_runtime(monkeypatch, report=partial, returncode=1)

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.complete is False
    assert "provider_analysis_incomplete" in evidence.degraded_reasons
    assert "provider_analysis_status=partial" in evidence.degraded_reasons
    assert evidence.network_usage["osv_network_access"] == "blocked"


@pytest.mark.parametrize(
    ("report", "malformed", "returncode", "expected_reason"),
    [
        (None, None, 2, "scanner_exit_code=2"),
        (None, b"{not-json", 0, "provider_report_missing_or_malformed"),
        ({"execution_successful": True, "issues": []}, None, 0, "provider_analysis_completeness_missing"),
    ],
)
def test_failure_and_malformed_states_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, object] | None,
    malformed: bytes | None,
    returncode: int,
    expected_reason: str,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(
        monkeypatch,
        report=report,
        malformed=malformed,
        returncode=returncode,
    )

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.complete is False
    assert expected_reason in evidence.degraded_reasons


def test_timeout_fails_closed_without_host_process_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(
        monkeypatch,
        scan_error=subprocess.TimeoutExpired(["docker"], 120),
    )

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.degraded_reasons == ("scanner_timeout",)


def test_image_identity_mismatch_fails_before_candidate_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report(), image_id="sha256:not-approved")

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.degraded_reasons == ("provider_image_identity_mismatch",)


def test_symlinked_candidate_is_rejected_before_scanner_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        (candidate.snapshot_path / "escape").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    monkeypatch.setattr(
        skillspector.shutil,
        "which",
        lambda _name: pytest.fail("scanner runtime must not be resolved for invalid input"),
    )

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.degraded_reasons == ("candidate_validation_failed:ValueError",)


def test_missing_container_runtime_is_degraded_and_never_runs_host_scanner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(skillspector.shutil, "which", lambda _name: None)

    evidence = _provider(tmp_path).scan(candidate)

    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.degraded_reasons == ("container_runtime_unavailable",)


def test_provider_removal_does_not_delete_historical_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report())
    repository = InMemorySecurityEvidenceRepository()
    service = SecurityEvidenceService(repository, (_provider(tmp_path),))
    evidence = service.scan(skillspector.PROVIDER_ID, candidate)

    service.unregister_provider(skillspector.PROVIDER_ID)

    assert repository.get(evidence.evidence_id) == evidence
    with pytest.raises(ContractError) as exc_info:
        service.scan(skillspector.PROVIDER_ID, candidate)
    assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_json_repository_preserves_immutable_historical_evidence(tmp_path: Path) -> None:
    path = tmp_path / "security-evidence.json"
    repository = JsonSecurityEvidenceRepository(path)
    evidence = SecurityEvidence(
        evidence_id=new_security_evidence_id(),
        provider=skillspector.PROVIDER_ID,
        provider_version=skillspector.PINNED_VERSION,
        provider_revision=skillspector.PINNED_REVISION,
        provider_build_identity=skillspector.PINNED_IMAGE_ID,
        dependency_set_digest=skillspector.PINNED_DEPENDENCY_SET_SHA256,
        scan_mode=skillspector.SCAN_MODE,
        policy_config_revision=skillspector.POLICY_CONFIG_REVISION,
        observed_at=skillspector.datetime(2026, 9, 12, tzinfo=skillspector.UTC),
        candidate_id="skill_external-example",
        candidate_revision=1,
        candidate_digest="a" * 64,
        status=SecurityEvidenceStatus.FINDINGS,
        complete=True,
        findings=(SecurityFinding("P1", "occurrence-1", severity="high"),),
        raw_report_digest="b" * 64,
        raw_report_artifact_ref="security-evidence://raw/report",
    )
    repository.add(evidence)

    reloaded = JsonSecurityEvidenceRepository(path)
    assert reloaded.get(evidence.evidence_id) == evidence

    changed = SecurityEvidence(
        **{
            **evidence.__dict__,
            "status": SecurityEvidenceStatus.DEGRADED,
            "complete": False,
            "degraded_reasons": ("changed",),
        }
    )
    with pytest.raises(ContractError, match="immutable"):
        reloaded.add(changed)


def test_review_projection_surfaces_degradation_provenance_and_limitations() -> None:
    evidence = SecurityEvidence(
        evidence_id=new_security_evidence_id(),
        provider=skillspector.PROVIDER_ID,
        provider_version=skillspector.PINNED_VERSION,
        provider_revision=skillspector.PINNED_REVISION,
        provider_build_identity=skillspector.PINNED_IMAGE_ID,
        dependency_set_digest=skillspector.PINNED_DEPENDENCY_SET_SHA256,
        scan_mode=skillspector.SCAN_MODE,
        policy_config_revision=skillspector.POLICY_CONFIG_REVISION,
        observed_at=skillspector.datetime(2026, 9, 12, tzinfo=skillspector.UTC),
        candidate_id="skill_external-example",
        candidate_revision=1,
        candidate_digest="a" * 64,
        status=SecurityEvidenceStatus.DEGRADED,
        complete=False,
        degraded_reasons=("provider_analysis_incomplete",),
        known_provider_limitations=skillspector.KNOWN_LIMITATIONS,
        raw_report_digest="b" * 64,
        raw_report_artifact_ref="security-evidence://raw/report",
    )

    resource = security_evidence_resource(evidence)

    assert resource["status"] == "degraded"
    assert resource["complete"] is False
    assert resource["degraded_reasons"] == ["provider_analysis_incomplete"]
    assert resource["provider_revision"] == skillspector.PINNED_REVISION
    assert resource["known_provider_limitations"] == list(skillspector.KNOWN_LIMITATIONS)
    assert resource["raw_report_artifact_ref"] == "security-evidence://raw/report"
