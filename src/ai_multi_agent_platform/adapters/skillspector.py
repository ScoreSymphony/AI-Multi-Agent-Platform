"""Optional NVIDIA SkillSpector static pre-install SecurityEvidence provider.

Only the #800-approved static/no-LLM/network-none path is implemented. The adapter
accepts a platform-staged local directory and never resolves Git/URL/archive sources.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.skills.security_evidence import (
    RawSecurityReportStore,
    SecurityEvidence,
    SecurityEvidenceStatus,
    SecurityFinding,
    StagedSkillCandidate,
    new_security_evidence_id,
)

PROVIDER_ID = "nvidia/skillspector"
PINNED_VERSION = "2.11.2"
PINNED_REVISION = "69dcdfb74487d361ba4c811d088cfdea2ff3a9dc"
PINNED_IMAGE_ID = "sha256:55abb78a1f1af1f430722920b96d4e24bed03e9e9811e1cf5330b859d2387fc1"
PINNED_DEPENDENCY_SET_SHA256 = "d6716d890040ac73494a8422ea51ef11229e71e0c0d9b538a95316a3a644fc61"
PINNED_DOCKERFILE_SHA256 = "124041bd2c81880747197f221c8d9a13b7378ac5ad98a17b4ab6a15ad22eb9aa"
PINNED_LICENSE_SHA256 = "9f8785b47596b2993a17a3fa8d747ae63126a2c5e80a9e77195a907273d71839"
EVALUATION_ARTIFACT_SHA256 = "5c3f7f39154d23a4a77151652e1eabf7ace68056b976f912877f2ebab3bdd760"
SCAN_MODE = "static_no_llm_network_none"
POLICY_CONFIG_REVISION = "skillspector-static-v1"
MAX_CAPTURE_BYTES = 256_000
SAFE_ENV_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "TMPDIR"})
KNOWN_LIMITATIONS = (
    "benign negation may trigger PE3 credential-access false positive",
    "static mode misses the evaluated memory-poisoning fixture",
    "file-read to network-send correlation can be incomplete",
    "autostart persistence write may lack a dedicated persistence finding",
    "overlapping/duplicate findings can occur",
    "network-disabled OSV supply-chain analysis can be partial/degraded",
)


@dataclass(frozen=True, slots=True)
class SkillSpectorConfig:
    enabled: bool = False
    image_ref: str = "skillspector-production:2.11.2"
    runtime: str | None = None
    provider_version: str = PINNED_VERSION
    provider_revision: str = PINNED_REVISION
    provider_build_identity: str = PINNED_IMAGE_ID
    dependency_set_digest: str = PINNED_DEPENDENCY_SET_SHA256
    scan_mode: str = SCAN_MODE
    policy_config_revision: str = POLICY_CONFIG_REVISION
    timeout_seconds: int = 120
    memory_limit: str = "1g"
    cpu_limit: str = "1.0"
    pids_limit: int = 256
    tmpfs_size: str = "64m"

    def __post_init__(self) -> None:
        if self.provider_version != PINNED_VERSION:
            raise ValueError("unsupported SkillSpector version; rerun #800 corpus before upgrade")
        if self.provider_revision != PINNED_REVISION:
            raise ValueError("unsupported SkillSpector revision; rerun #800 corpus before upgrade")
        if self.provider_build_identity != PINNED_IMAGE_ID:
            raise ValueError(
                "SkillSpector built-image identity does not match approved production pin"
            )
        if self.dependency_set_digest != PINNED_DEPENDENCY_SET_SHA256:
            raise ValueError("SkillSpector dependency lock does not match approved production pin")
        if self.scan_mode != SCAN_MODE:
            raise ValueError("only static_no_llm_network_none is supported")
        if self.policy_config_revision != POLICY_CONFIG_REVISION:
            raise ValueError("unsupported SkillSpector policy/config revision")
        if not self.image_ref.strip():
            raise ValueError("SkillSpector image_ref must not be blank")
        if self.runtime not in {None, "docker", "podman"}:
            raise ValueError("SkillSpector runtime must be docker, podman or None")
        if self.timeout_seconds <= 0 or self.pids_limit <= 0:
            raise ValueError("SkillSpector timeout and PID limit must be positive")
        if (
            not self.memory_limit.strip()
            or not self.cpu_limit.strip()
            or not self.tmpfs_size.strip()
        ):
            raise ValueError("SkillSpector resource limits must be explicit")


class SkillSpectorSecurityEvidenceProvider:
    """Container-isolated advisory scanner; never a canonical trust authority."""

    def __init__(
        self,
        config: SkillSpectorConfig,
        raw_report_store: RawSecurityReportStore,
    ) -> None:
        self.config = config
        self.raw_report_store = raw_report_store

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def scan(self, candidate: StagedSkillCandidate) -> SecurityEvidence:
        observed_at = datetime.now(UTC)
        source = candidate.snapshot_path
        try:
            source = _validate_candidate(source, candidate.candidate_digest)
        except (OSError, ValueError) as exc:
            return self._degraded(
                candidate, observed_at, f"candidate_validation_failed:{type(exc).__name__}"
            )

        runtime = self._resolve_runtime()
        if runtime is None:
            return self._degraded(candidate, observed_at, "container_runtime_unavailable")

        identity_reason = self._verify_image_identity(runtime)
        if identity_reason is not None:
            return self._degraded(candidate, observed_at, identity_reason)

        with tempfile.TemporaryDirectory(prefix="skillspector-868-") as temp:
            output = Path(temp) / "output"
            _prepare_output_directory(output)
            command = build_container_command(runtime, self.config, source, output)
            try:
                completed = _run(command, timeout_seconds=self.config.timeout_seconds)
            except subprocess.TimeoutExpired:
                return self._degraded(candidate, observed_at, "scanner_timeout")
            except OSError:
                return self._degraded(candidate, observed_at, "scanner_process_start_failed")

            report_path = output / "report.json"
            raw_bytes: bytes | None = None
            report: Mapping[str, object] | None = None
            if report_path.exists():
                try:
                    raw_bytes = report_path.read_bytes()
                    decoded = raw_bytes.decode("utf-8")
                    value = json.loads(decoded)
                    if isinstance(value, Mapping):
                        report = value
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    report = None

            post_digest = digest_tree(source)
            extra_reasons: list[str] = []
            if post_digest != candidate.candidate_digest:
                extra_reasons.append("candidate_snapshot_changed_during_scan")

            artifact_digest: str | None = None
            artifact_ref: str | None = None
            if raw_bytes is not None:
                artifact = self.raw_report_store.put(raw_bytes)
                artifact_digest = artifact.digest
                artifact_ref = artifact.artifact_ref

            return self._normalize(
                candidate,
                observed_at=observed_at,
                returncode=completed.returncode,
                report=report,
                raw_report_digest=artifact_digest,
                raw_report_artifact_ref=artifact_ref,
                extra_reasons=tuple(extra_reasons),
            )

    def _resolve_runtime(self) -> str | None:
        requested = self.config.runtime
        if requested is not None:
            return shutil.which(requested)
        return shutil.which("docker") or shutil.which("podman")

    def _verify_image_identity(self, runtime: str) -> str | None:
        command = [runtime, "image", "inspect", "--format", "{{.Id}}", self.config.image_ref]
        try:
            completed = _run(command, timeout_seconds=min(self.config.timeout_seconds, 30))
        except subprocess.TimeoutExpired:
            return "provider_image_inspect_timeout"
        except OSError:
            return "provider_image_inspect_failed"
        if completed.returncode != 0:
            return "provider_image_unavailable"
        observed = completed.stdout.strip()
        if observed != self.config.provider_build_identity:
            return "provider_image_identity_mismatch"
        return None

    def _normalize(
        self,
        candidate: StagedSkillCandidate,
        *,
        observed_at: datetime,
        returncode: int,
        report: Mapping[str, object] | None,
        raw_report_digest: str | None,
        raw_report_artifact_ref: str | None,
        extra_reasons: tuple[str, ...] = (),
    ) -> SecurityEvidence:
        reasons = list(extra_reasons)
        findings: tuple[SecurityFinding, ...] = ()
        suppressions: tuple[Mapping[str, JsonValue], ...] = ()
        baseline_metadata: dict[str, JsonValue] = {}
        provider_metadata: dict[str, JsonValue] = {}
        complete = True

        if returncode not in {0, 1}:
            complete = False
            reasons.append(f"scanner_exit_code={returncode}")
        if report is None:
            complete = False
            reasons.append("provider_report_missing_or_malformed")
        else:
            if report.get("execution_successful") is not True:
                complete = False
                reasons.append("provider_execution_unsuccessful")
            finding_values = _finding_values(report)
            if finding_values is None:
                complete = False
                reasons.append("provider_findings_missing")
            elif not all(isinstance(item, Mapping) for item in finding_values):
                complete = False
                reasons.append("provider_findings_invalid")
            else:
                findings = tuple(_normalize_finding(item) for item in finding_values)

            completeness = report.get("analysis_completeness")
            if not isinstance(completeness, Mapping):
                complete = False
                reasons.append("provider_analysis_completeness_missing")
            else:
                provider_metadata["analysis_completeness"] = _json_mapping(completeness)
                if completeness.get("is_complete") is not True:
                    complete = False
                    reasons.append("provider_analysis_incomplete")
                if completeness.get("execution_successful") is False:
                    complete = False
                    reasons.append("provider_analysis_execution_failed")
                status = completeness.get("status")
                if isinstance(status, str) and status.lower() not in {
                    "complete",
                    "completed",
                    "full",
                }:
                    complete = False
                    reasons.append(f"provider_analysis_status={status.lower()}")

            risk = report.get("risk_assessment")
            if isinstance(risk, Mapping):
                provider_metadata["risk_assessment"] = _json_mapping(risk)
            metadata = report.get("metadata")
            if isinstance(metadata, Mapping):
                provider_metadata["metadata"] = _json_mapping(metadata)
            skill_metadata = report.get("skill")
            if isinstance(skill_metadata, Mapping):
                provider_metadata["skill"] = _json_mapping(skill_metadata)
            provider_metadata["scanner_exit_code"] = returncode
            provider_metadata["provider_native_recommendation_is_advisory"] = True
            provider_metadata["pinned_dockerfile_sha256"] = PINNED_DOCKERFILE_SHA256
            provider_metadata["pinned_license_sha256"] = PINNED_LICENSE_SHA256
            provider_metadata["evaluation_artifact_sha256"] = EVALUATION_ARTIFACT_SHA256

            baseline = report.get("baseline")
            if isinstance(baseline, Mapping):
                baseline_metadata = _json_mapping(baseline)

            suppressed = report.get("suppressed")
            if isinstance(suppressed, list):
                suppressions = tuple(
                    _json_mapping(item) for item in suppressed if isinstance(item, Mapping)
                )

        if raw_report_digest is None:
            complete = False
            reasons.append("raw_report_not_retained")
        if not complete:
            status = SecurityEvidenceStatus.DEGRADED
        elif findings:
            status = SecurityEvidenceStatus.FINDINGS
        else:
            status = SecurityEvidenceStatus.CLEAN

        return SecurityEvidence(
            evidence_id=new_security_evidence_id(),
            provider=PROVIDER_ID,
            provider_version=self.config.provider_version,
            provider_revision=self.config.provider_revision,
            provider_build_identity=self.config.provider_build_identity,
            dependency_set_digest=self.config.dependency_set_digest,
            scan_mode=self.config.scan_mode,
            policy_config_revision=self.config.policy_config_revision,
            observed_at=_report_observed_at(report, observed_at),
            candidate_id=candidate.candidate_id,
            candidate_revision=candidate.candidate_revision,
            candidate_digest=candidate.candidate_digest,
            status=status,
            complete=complete,
            findings=findings,
            degraded_reasons=tuple(dict.fromkeys(reasons)),
            suppression_metadata=suppressions,
            baseline_metadata=baseline_metadata,
            network_usage={
                "container_network": "none",
                "osv_network_access": "blocked",
                "external_llm_network_access": "disabled",
            },
            provider_usage={
                "llm_assisted": False,
                "external_provider": None,
                "hosted_service_required": False,
            },
            provider_metadata=provider_metadata,
            known_provider_limitations=KNOWN_LIMITATIONS,
            raw_report_digest=raw_report_digest,
            raw_report_artifact_ref=raw_report_artifact_ref,
        )

    def _degraded(
        self,
        candidate: StagedSkillCandidate,
        observed_at: datetime,
        reason: str,
    ) -> SecurityEvidence:
        return SecurityEvidence(
            evidence_id=new_security_evidence_id(),
            provider=PROVIDER_ID,
            provider_version=self.config.provider_version,
            provider_revision=self.config.provider_revision,
            provider_build_identity=self.config.provider_build_identity,
            dependency_set_digest=self.config.dependency_set_digest,
            scan_mode=self.config.scan_mode,
            policy_config_revision=self.config.policy_config_revision,
            observed_at=observed_at,
            candidate_id=candidate.candidate_id,
            candidate_revision=candidate.candidate_revision,
            candidate_digest=candidate.candidate_digest,
            status=SecurityEvidenceStatus.DEGRADED,
            complete=False,
            degraded_reasons=(reason,),
            network_usage={
                "container_network": "none",
                "osv_network_access": "blocked",
                "external_llm_network_access": "disabled",
            },
            provider_usage={
                "llm_assisted": False,
                "external_provider": None,
                "hosted_service_required": False,
            },
            provider_metadata={
                "provider_native_recommendation_is_advisory": True,
                "pinned_dockerfile_sha256": PINNED_DOCKERFILE_SHA256,
                "pinned_license_sha256": PINNED_LICENSE_SHA256,
                "evaluation_artifact_sha256": EVALUATION_ARTIFACT_SHA256,
            },
            known_provider_limitations=KNOWN_LIMITATIONS,
        )


def build_container_command(
    runtime: str,
    config: SkillSpectorConfig,
    input_dir: Path,
    output_dir: Path,
) -> list[str]:
    """Build the only supported production scan command.

    Candidate input is a read-only bind mount; the container filesystem is read-only,
    networking is disabled, all capabilities are dropped and candidate code is never run.
    """
    return [
        runtime,
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={config.pids_limit}",
        f"--memory={config.memory_limit}",
        f"--cpus={config.cpu_limit}",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,size={config.tmpfs_size}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{input_dir}:/scan:ro",
        "-v",
        f"{output_dir}:/out:rw",
        "--entrypoint",
        "skillspector",
        config.image_ref,
        "scan",
        "/scan",
        "--no-llm",
        "--format",
        "json",
        "--output",
        "/out/report.json",
    ]


def sanitized_environment() -> dict[str, str]:
    """Forward only non-secret process essentials; provider/API credentials are excluded."""
    return {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}


def digest_tree(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_candidate(source: Path, expected_digest: str) -> Path:
    if source.is_symlink():
        raise ValueError("staged Skill candidate root cannot be a symlink")
    resolved = source.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("staged Skill candidate must be a local directory")
    for path in resolved.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"staged Skill candidate contains unsupported symlink: {path}")
        path.resolve(strict=True).relative_to(resolved)
    observed_digest = digest_tree(resolved)
    if observed_digest != expected_digest:
        raise ValueError("staged Skill candidate digest does not match canonical revision")
    return resolved


def _prepare_output_directory(path: Path) -> None:
    path.mkdir()
    if os.name == "posix":
        path.chmod(0o733)


def _run(command: Sequence[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=sanitized_environment(),
    )
    if len(completed.stdout.encode()) > MAX_CAPTURE_BYTES:
        completed.stdout = completed.stdout.encode()[:MAX_CAPTURE_BYTES].decode(errors="replace")
    if len(completed.stderr.encode()) > MAX_CAPTURE_BYTES:
        completed.stderr = completed.stderr.encode()[:MAX_CAPTURE_BYTES].decode(errors="replace")
    return completed


def _finding_values(report: Mapping[str, object]) -> list[object] | None:
    for key in ("issues", "findings", "results"):
        value = report.get(key)
        if isinstance(value, list):
            return value
    return None


def _normalize_finding(value: object) -> SecurityFinding:
    item = value if isinstance(value, Mapping) else {}
    location = item.get("location") if isinstance(item.get("location"), Mapping) else {}
    confidence = _optional_float(item.get("confidence"))
    line = _optional_int(location.get("start_line") or location.get("line"))
    if line is None:
        line = _optional_int(item.get("start_line") or item.get("line") or item.get("line_number"))
    severity = _first(item, "severity", "risk_level", "level")
    provider_metadata: dict[str, JsonValue] = {}
    for key in ("pattern", "remediation", "intent", "match_fingerprint", "tags", "evidence"):
        if key in item:
            provider_metadata[key] = _json_value(item[key])
    return SecurityFinding(
        rule_id=_optional_str(_first(item, "id", "rule_id")),
        occurrence_id=_optional_str(_first(item, "finding_id", "provider_finding_id")),
        category=_optional_str(_first(item, "category", "type", "kind")),
        severity=str(severity).lower() if severity is not None else None,
        confidence=confidence,
        summary=_optional_str(
            _first(item, "title", "summary", "message", "finding", "description", "explanation")
        ),
        path=_optional_str(
            location.get("file") or location.get("path") or item.get("file") or item.get("path")
        ),
        line=line,
        metadata=provider_metadata,
    )


def _report_observed_at(
    report: Mapping[str, object] | None,
    fallback: datetime,
) -> datetime:
    if report is not None:
        skill = report.get("skill")
        if isinstance(skill, Mapping):
            scanned_at = skill.get("scanned_at")
            if isinstance(scanned_at, str) and scanned_at:
                try:
                    return datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
                except ValueError:
                    pass
    return fallback


def _json_mapping(value: Mapping[object, object]) -> dict[str, JsonValue]:
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _first(mapping: Mapping[object, object], *keys: str) -> object | None:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
