from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_multi_agent_platform.adapters.skillspector as skillspector
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.skills.security_evidence import (
    FilesystemRawSecurityReportStore,
    InMemorySecurityEvidenceRepository,
    JsonSecurityEvidenceRepository,
    RawReportArtifact,
    SecurityEvidence,
    SecurityEvidenceService,
    SecurityEvidenceStatus,
    SecurityFinding,
    StagedSkillCandidate,
    new_security_evidence_id,
)
from ai_multi_agent_platform.skills.security_evidence_control_plane import (
    SkillSecurityEvidenceResourceService,
    security_evidence_resource,
)

OBSERVED_IMAGE_ID = "sha256:" + "1" * 64


def _candidate(tmp_path: Path, revision: int = 3) -> StagedSkillCandidate:
    root = tmp_path / f"candidate-{revision}"
    root.mkdir()
    (root / "SKILL.md").write_text("# Example\nUse local files only.\n", encoding="utf-8")
    return StagedSkillCandidate(
        "skill_external-example",
        revision,
        skillspector.digest_tree(root),
        root,
    )


def _report(
    *,
    findings: list[object] | None = None,
    complete: bool = True,
    status: str = "complete",
) -> dict[str, object]:
    values = [] if findings is None else findings
    return {
        "execution_successful": True,
        "skill": {"scanned_at": "2026-09-12T18:00:00+00:00"},
        "analysis_completeness": {
            "is_complete": complete,
            "execution_successful": True,
            "status": status,
        },
        "issues": values,
        "suppressed": [{"rule_id": "P9", "reason": "reviewed baseline"}],
        "baseline": {"id": "baseline-v1", "accepted_count": 1},
        "risk_assessment": {
            "risk_score": 0 if not values else 100,
            "risk_severity": "LOW" if not values else "CRITICAL",
            "recommendation": "SAFE" if not values else "DO_NOT_INSTALL",
        },
    }


def _image_payload(
    image_id: str = OBSERVED_IMAGE_ID,
    *,
    revision: str = skillspector.PINNED_REVISION,
) -> str:
    return json.dumps(
        {
            "Id": image_id,
            "Config": {
                "Labels": {
                    skillspector.LABEL_REVISION: revision,
                    skillspector.LABEL_DEPENDENCIES: (
                        skillspector.PINNED_DEPENDENCY_SET_SHA256
                    ),
                    skillspector.LABEL_MODE: skillspector.SCAN_MODE,
                    skillspector.LABEL_POLICY: skillspector.POLICY_CONFIG_REVISION,
                }
            },
        }
    )


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    report: dict[str, object] | None = None,
    malformed: bytes | None = None,
    returncode: int = 0,
    image_payload: str | None = None,
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
            return subprocess.CompletedProcess(
                values,
                0,
                image_payload or _image_payload(),
                "",
            )
        if scan_error is not None:
            raise scan_error
        output_mount = next(
            values[index + 1]
            for index, value in enumerate(values[:-1])
            if value == "-v" and values[index + 1].endswith(":/out:rw")
        )
        output = Path(output_mount.removesuffix(":/out:rw"))
        if malformed is not None:
            (output / "report.json").write_bytes(malformed)
        elif report is not None:
            (output / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(values, returncode, "", "")

    monkeypatch.setattr(skillspector, "_run", fake_run)


class _FailingRawStore:
    def put(self, payload: bytes) -> RawReportArtifact:
        del payload
        raise RuntimeError("retention backend unavailable")


def _provider(
    tmp_path: Path,
    *,
    enabled: bool = True,
    expected_image_id: str | None = None,
    raw_store: object | None = None,
) -> skillspector.SkillSpectorSecurityEvidenceProvider:
    store = raw_store or FilesystemRawSecurityReportStore(tmp_path / "raw-reports")
    return skillspector.SkillSpectorSecurityEvidenceProvider(
        skillspector.SkillSpectorConfig(
            enabled=enabled,
            runtime="docker",
            expected_image_id=expected_image_id,
        ),
        store,  # type: ignore[arg-type]
    )


def _evidence(
    *,
    evidence_id: str | None = None,
    candidate_id: str = "skill_external-example",
    revision: int = 1,
    status: SecurityEvidenceStatus = SecurityEvidenceStatus.DEGRADED,
) -> SecurityEvidence:
    complete = status is not SecurityEvidenceStatus.DEGRADED
    findings = (
        (SecurityFinding("P1", "occurrence-1", severity="high"),)
        if status is SecurityEvidenceStatus.FINDINGS
        else ()
    )
    return SecurityEvidence(
        evidence_id=evidence_id or new_security_evidence_id(),
        provider=skillspector.PROVIDER_ID,
        provider_version=skillspector.PINNED_VERSION,
        provider_revision=skillspector.PINNED_REVISION,
        provider_build_identity=OBSERVED_IMAGE_ID,
        dependency_set_digest=skillspector.PINNED_DEPENDENCY_SET_SHA256,
        scan_mode=skillspector.SCAN_MODE,
        policy_config_revision=skillspector.POLICY_CONFIG_REVISION,
        observed_at=datetime(2026, 9, 12, tzinfo=UTC),
        candidate_id=candidate_id,
        candidate_revision=revision,
        candidate_digest="a" * 64,
        status=status,
        complete=complete,
        findings=findings,
        degraded_reasons=("provider_analysis_incomplete",) if not complete else (),
        known_provider_limitations=skillspector.KNOWN_LIMITATIONS,
        raw_report_digest="b" * 64,
        raw_report_artifact_ref="security-evidence://raw/report",
    )


def test_production_pin_rejects_unreviewed_provider_drift() -> None:
    with pytest.raises(ValueError, match="rerun #800 corpus"):
        skillspector.SkillSpectorConfig(provider_revision="deadbeef")
    with pytest.raises(ValueError, match="dependency lock"):
        skillspector.SkillSpectorConfig(dependency_set_digest="0" * 64)
    with pytest.raises(ValueError, match="only static_no_llm_network_none"):
        skillspector.SkillSpectorConfig(scan_mode="llm_assisted")
    with pytest.raises(ValueError, match="OCI sha256"):
        skillspector.SkillSpectorConfig(expected_image_id="latest")


def test_default_provider_is_disabled_and_absence_does_not_block_lifecycle(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    repository = InMemorySecurityEvidenceRepository()
    service = SecurityEvidenceService(repository, (_provider(tmp_path, enabled=False),))
    with pytest.raises(ContractError) as exc:
        service.scan(skillspector.PROVIDER_ID, candidate)
    assert exc.value.code is ErrorCode.UNAVAILABLE
    assert repository.list_all() == ()


def test_container_command_is_static_no_llm_network_none_and_resource_bounded(
    tmp_path: Path,
) -> None:
    command = skillspector.build_container_command(
        "docker",
        skillspector.SkillSpectorConfig(enabled=True),
        tmp_path / "candidate",
        tmp_path / "output",
    )
    for value in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--memory=1g",
        "--cpus=1.0",
        "--no-llm",
        "HOME=/tmp",
    ):
        assert value in command
    assert f"{tmp_path / 'candidate'}:/scan:ro" in command
    assert "/tmp:rw,noexec,nosuid,size=64m" in command


def test_sanitized_environment_never_forwards_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    credential_keys = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "NVIDIA_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
    )
    for key in credential_keys:
        monkeypatch.setenv(key, "secret")
    environment = skillspector.sanitized_environment()
    assert environment["PATH"] == "/usr/bin"
    assert all(key not in environment for key in credential_keys)


def test_clean_scan_records_observed_image_and_is_advisory_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report())
    repository = InMemorySecurityEvidenceRepository()
    evidence = SecurityEvidenceService(repository, (_provider(tmp_path),)).scan(
        skillspector.PROVIDER_ID,
        candidate,
    )
    assert evidence.status is SecurityEvidenceStatus.CLEAN
    assert evidence.complete is True
    assert evidence.provider_build_identity == OBSERVED_IMAGE_ID
    assert evidence.raw_report_digest is not None
    assert evidence.network_usage["container_network"] == "none"
    assert evidence.provider_usage["llm_assisted"] is False
    assert not hasattr(evidence, "trust_status")
    assert not hasattr(evidence, "approved")
    assert repository.list_for_candidate(candidate.candidate_id, 3) == (evidence,)


def test_high_risk_recommendation_stays_metadata_and_rule_identity_is_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = {
        "id": "AST4",
        "finding_id": "finding-run-123",
        "category": "dangerous_code",
        "severity": "CRITICAL",
        "confidence": 0.99,
        "finding": "subprocess execution",
        "location": {"file": "tool.py", "start_line": 7},
    }
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report(findings=[finding]), returncode=1)
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.status is SecurityEvidenceStatus.FINDINGS
    assert evidence.findings[0].rule_id == "AST4"
    assert evidence.findings[0].occurrence_id == "finding-run-123"
    assert evidence.findings[0].path == "tool.py"
    assert evidence.findings[0].line == 7
    risk = evidence.provider_metadata["risk_assessment"]
    assert isinstance(risk, Mapping)
    assert risk["recommendation"] == "DO_NOT_INSTALL"
    assert evidence.provider_metadata["provider_native_recommendation_is_advisory"] is True
    assert not hasattr(evidence, "reject_skill")


def test_suppression_baseline_and_nested_metadata_are_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    report = _report()
    report["metadata"] = {"nested": {"items": ["one", "two"]}}
    _install_fake_runtime(monkeypatch, report=report)
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.suppression_metadata[0]["rule_id"] == "P9"
    assert evidence.baseline_metadata["id"] == "baseline-v1"
    nested = evidence.provider_metadata["metadata"]
    assert isinstance(nested, Mapping)
    with pytest.raises(TypeError):
        nested["nested"] = {}  # type: ignore[index]
    nested_value = nested["nested"]
    assert isinstance(nested_value, Mapping)
    with pytest.raises(TypeError):
        nested_value["items"] = []  # type: ignore[index]
    assert not hasattr(evidence, "approval_id")


def test_offline_partial_supply_chain_is_degraded_not_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(
        monkeypatch,
        report=_report(findings=[{"id": "SC4"}], complete=False, status="partial"),
        returncode=1,
    )
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert "provider_analysis_incomplete" in evidence.degraded_reasons
    assert "provider_analysis_status=partial" in evidence.degraded_reasons
    assert evidence.network_usage["osv_network_access"] == "blocked"


@pytest.mark.parametrize(
    ("report", "malformed", "returncode", "reason"),
    [
        (None, None, 2, "scanner_exit_code=2"),
        (None, b"{bad", 0, "provider_report_missing_or_malformed"),
        (
            {"execution_successful": True, "issues": []},
            None,
            0,
            "provider_analysis_completeness_missing",
        ),
        (_report(findings=["not-an-object"]), None, 0, "provider_findings_invalid"),
    ],
)
def test_malformed_or_incomplete_provider_results_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, object] | None,
    malformed: bytes | None,
    returncode: int,
    reason: str,
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
    assert reason in evidence.degraded_reasons


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (subprocess.TimeoutExpired(["docker"], 120), "scanner_timeout"),
        (OSError("boom"), "scanner_process_start_failed"),
    ],
)
def test_timeout_and_process_failure_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    reason: str,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, scan_error=error)
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.degraded_reasons == (reason,)


def test_raw_report_retention_failure_is_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(monkeypatch, report=_report())
    evidence = _provider(tmp_path, raw_store=_FailingRawStore()).scan(candidate)
    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.complete is False
    assert "raw_report_retention_failed" in evidence.degraded_reasons
    assert "raw_report_not_retained" in evidence.degraded_reasons


def test_image_identity_guards_fail_before_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    _install_fake_runtime(
        monkeypatch,
        report=_report(),
        image_payload=_image_payload(revision="wrong"),
    )
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.degraded_reasons == ("provider_image_manifest_mismatch",)

    _install_fake_runtime(monkeypatch, report=_report())
    evidence = _provider(tmp_path, expected_image_id="sha256:" + "2" * 64).scan(candidate)
    assert evidence.degraded_reasons == ("provider_image_identity_mismatch",)


def test_non_local_or_symlink_candidate_is_rejected_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    try:
        (candidate.snapshot_path / "escape").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(
        skillspector.shutil,
        "which",
        lambda _name: pytest.fail("runtime must not be resolved"),
    )
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.degraded_reasons == ("candidate_validation_failed:ValueError",)

    remote = StagedSkillCandidate(
        "skill_external-url",
        1,
        "a" * 64,
        Path("https:/example.invalid/repo.git"),
    )
    evidence = _provider(tmp_path).scan(remote)
    assert evidence.status is SecurityEvidenceStatus.DEGRADED
    assert evidence.degraded_reasons[0].startswith("candidate_validation_failed:")


def test_missing_container_runtime_has_no_host_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(skillspector.shutil, "which", lambda _name: None)
    evidence = _provider(tmp_path).scan(candidate)
    assert evidence.degraded_reasons == ("container_runtime_unavailable",)


def test_provider_removal_and_rescan_keep_historical_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemorySecurityEvidenceRepository()
    service = SecurityEvidenceService(repository, (_provider(tmp_path),))
    first_candidate = _candidate(tmp_path, 1)
    _install_fake_runtime(monkeypatch, report=_report())
    first = service.scan(skillspector.PROVIDER_ID, first_candidate)
    second = service.scan(skillspector.PROVIDER_ID, _candidate(tmp_path, 2))
    assert first.evidence_id != second.evidence_id
    assert repository.get(first.evidence_id) == first
    assert repository.list_for_candidate(first.candidate_id, 1) == (first,)

    service.unregister_provider(skillspector.PROVIDER_ID)
    assert repository.get(first.evidence_id) == first
    with pytest.raises(ContractError) as exc:
        service.scan(skillspector.PROVIDER_ID, first_candidate)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_json_repository_rejects_mutation_of_existing_evidence_id(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    repository = JsonSecurityEvidenceRepository(path)
    evidence = _evidence(status=SecurityEvidenceStatus.FINDINGS)
    repository.add(evidence)
    reloaded = JsonSecurityEvidenceRepository(path)
    assert reloaded.get(evidence.evidence_id) == evidence
    changed = replace(
        evidence,
        status=SecurityEvidenceStatus.DEGRADED,
        complete=False,
        findings=(),
        degraded_reasons=("changed",),
    )
    with pytest.raises(ContractError, match="immutable"):
        reloaded.add(changed)


def test_review_projection_exposes_provenance_degradation_and_limitations() -> None:
    evidence = _evidence()
    resource = security_evidence_resource(evidence)
    assert resource["status"] == "degraded"
    assert resource["provider_build_identity"] == OBSERVED_IMAGE_ID
    assert resource["degraded_reasons"] == ["provider_analysis_incomplete"]
    assert resource["known_provider_limitations"] == list(skillspector.KNOWN_LIMITATIONS)


class _ScopedSkillRepository:
    def __init__(self, revisions: dict[tuple[str, int], object]) -> None:
        self.revisions = revisions

    def get_skill_revision(self, skill_id: str, revision: int) -> object:
        return self.revisions[(skill_id, revision)]


class _ScopeAccess:
    def __init__(self, visible_owner_id: str) -> None:
        self.visible_owner_id = visible_owner_id
        self.authorized: list[tuple[str, str]] = []

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None,
    ) -> bool:
        del context, action, resource_ref, project_id
        return owner_ref.id == self.visible_owner_id

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None,
    ) -> None:
        del context, project_id
        if owner_ref.id != self.visible_owner_id:
            raise ContractError(ErrorCode.FORBIDDEN, "cross-owner security evidence read denied")
        self.authorized.append((action, resource_ref))


def test_control_plane_security_evidence_is_filtered_by_canonical_skill_owner() -> None:
    first = _evidence(evidence_id="security_evidence_first", candidate_id="skill_first")
    second = _evidence(evidence_id="security_evidence_second", candidate_id="skill_second")
    repository = InMemorySecurityEvidenceRepository()
    repository.add(first)
    repository.add(second)
    evidence_service = SecurityEvidenceService(repository)
    revisions = {
        ("skill_first", 1): SimpleNamespace(
            owner_ref=OwnerRef(type="user", id="owner-a"),
            project_id="project_alpha",
            workspace_id="workspace_alpha",
        ),
        ("skill_second", 1): SimpleNamespace(
            owner_ref=OwnerRef(type="user", id="owner-b"),
            project_id="project_beta",
            workspace_id="workspace_beta",
        ),
    }
    skills = SimpleNamespace(repository=_ScopedSkillRepository(revisions))
    scope = _ScopeAccess("owner-a")
    service = SkillSecurityEvidenceResourceService(
        evidence_service,
        skills,  # type: ignore[arg-type]
        scope,  # type: ignore[arg-type]
    )
    context = RequestContext("request-868", "correlation-868")

    resources = asyncio.run(service.list_resources(context, PageQuery()))
    assert [resource["id"] for resource in resources] == [first.evidence_id]
    assert resources[0]["candidate_scope"] == {
        "owner_type": "user",
        "owner_id": "owner-a",
        "project_id": "project_alpha",
        "workspace_id": "workspace_alpha",
    }

    with pytest.raises(ContractError) as exc:
        asyncio.run(service.get_resource(context, second.evidence_id))
    assert exc.value.code is ErrorCode.FORBIDDEN
