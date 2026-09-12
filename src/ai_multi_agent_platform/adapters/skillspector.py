"""Optional NVIDIA SkillSpector static pre-install evidence provider."""

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
EVALUATED_IMAGE_ID = "sha256:55abb78a1f1af1f430722920b96d4e24bed03e9e9811e1cf5330b859d2387fc1"
PINNED_IMAGE_ID = EVALUATED_IMAGE_ID
PINNED_DEPENDENCY_SET_SHA256 = "d6716d890040ac73494a8422ea51ef11229e71e0c0d9b538a95316a3a644fc61"
PINNED_DOCKERFILE_SHA256 = "124041bd2c81880747197f221c8d9a13b7378ac5ad98a17b4ab6a15ad22eb9aa"
PINNED_LICENSE_SHA256 = "9f8785b47596b2993a17a3fa8d747ae63126a2c5e80a9e77195a907273d71839"
PRODUCTION_DOCKERFILE_SHA256 = "69f5a47d8d9b9551ef0b6719733d585a196d73877054ba0300e90dda7bccfa2a"
PRODUCTION_REQUIREMENTS_SHA256 = "85e25c541f966f1a2d513b0be5ce8d92a54bbec6b45b635d49f36cbf817f76c7"
EVALUATION_ARTIFACT_SHA256 = "5c3f7f39154d23a4a77151652e1eabf7ace68056b976f912877f2ebab3bdd760"
SCAN_MODE = "static_no_llm_network_none"
POLICY_CONFIG_REVISION = "skillspector-static-v1"
MAX_CAPTURE_BYTES = 256_000
SAFE_ENV_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "TMPDIR"})
LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_DEPENDENCIES = "io.scoresymphony.skillspector.dependency-set-sha256"
LABEL_MODE = "io.scoresymphony.skillspector.scan-mode"
LABEL_POLICY = "io.scoresymphony.skillspector.policy-config-revision"
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
    dependency_set_digest: str = PINNED_DEPENDENCY_SET_SHA256
    scan_mode: str = SCAN_MODE
    policy_config_revision: str = POLICY_CONFIG_REVISION
    expected_image_id: str | None = None
    timeout_seconds: int = 120
    memory_limit: str = "1g"
    cpu_limit: str = "1.0"
    pids_limit: int = 256
    tmpfs_size: str = "64m"

    def __post_init__(self) -> None:
        if self.provider_version != PINNED_VERSION or self.provider_revision != PINNED_REVISION:
            raise ValueError("unsupported SkillSpector pin; rerun #800 corpus before upgrade")
        if self.dependency_set_digest != PINNED_DEPENDENCY_SET_SHA256:
            raise ValueError("SkillSpector dependency lock does not match approved production pin")
        if self.scan_mode != SCAN_MODE:
            raise ValueError("only static_no_llm_network_none is supported")
        if self.policy_config_revision != POLICY_CONFIG_REVISION:
            raise ValueError("unsupported SkillSpector policy/config revision")
        if self.expected_image_id is not None and not self.expected_image_id.startswith("sha256:"):
            raise ValueError("expected_image_id must be an OCI sha256 image ID")
        if self.runtime not in {None, "docker", "podman"}:
            raise ValueError("runtime must be docker, podman or None")
        if not self.image_ref.strip() or self.timeout_seconds <= 0 or self.pids_limit <= 0:
            raise ValueError("SkillSpector runtime configuration is invalid")


@dataclass(frozen=True, slots=True)
class ImageInspection:
    image_id: str
    labels: Mapping[str, str]


class SkillSpectorSecurityEvidenceProvider:
    """Container-only advisory scanner; canonical Skill trust remains platform-owned."""

    def __init__(
        self, config: SkillSpectorConfig, raw_report_store: RawSecurityReportStore
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
        try:
            source = _validate_candidate(candidate.snapshot_path, candidate.candidate_digest)
        except (OSError, ValueError) as exc:
            return self._degraded(
                candidate,
                observed_at,
                f"candidate_validation_failed:{type(exc).__name__}",
            )
        runtime = self._resolve_runtime()
        if runtime is None:
            return self._degraded(candidate, observed_at, "container_runtime_unavailable")
        inspection, reason = self._inspect_image(runtime)
        if inspection is None:
            return self._degraded(candidate, observed_at, reason or "provider_image_invalid")
        with tempfile.TemporaryDirectory(prefix="skillspector-868-") as temp:
            output = Path(temp) / "output"
            _prepare_output_directory(output)
            command = build_container_command(runtime, self.config, source, output)
            try:
                completed = _run(command, timeout_seconds=self.config.timeout_seconds)
            except subprocess.TimeoutExpired:
                return self._degraded(
                    candidate, observed_at, "scanner_timeout", inspection.image_id
                )
            except OSError:
                return self._degraded(
                    candidate,
                    observed_at,
                    "scanner_process_start_failed",
                    inspection.image_id,
                )
            report_path = output / "report.json"
            raw_bytes: bytes | None = None
            report: Mapping[str, object] | None = None
            if report_path.exists():
                try:
                    raw_bytes = report_path.read_bytes()
                    decoded: object = json.loads(raw_bytes.decode("utf-8"))
                    if isinstance(decoded, Mapping):
                        report = decoded
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    report = None
            reasons: list[str] = []
            try:
                if _validate_candidate(source, candidate.candidate_digest) != source:
                    reasons.append("candidate_snapshot_changed_during_scan")
            except (OSError, ValueError):
                reasons.append("candidate_snapshot_changed_during_scan")
            raw_digest: str | None = None
            raw_ref: str | None = None
            if raw_bytes is not None:
                try:
                    artifact = self.raw_report_store.put(raw_bytes)
                except Exception:
                    reasons.append("raw_report_retention_failed")
                else:
                    raw_digest, raw_ref = artifact.digest, artifact.artifact_ref
            return self._normalize(
                candidate,
                inspection.image_id,
                observed_at,
                completed.returncode,
                report,
                raw_digest,
                raw_ref,
                tuple(reasons),
            )

    def _resolve_runtime(self) -> str | None:
        if self.config.runtime is not None:
            return shutil.which(self.config.runtime)
        return shutil.which("docker") or shutil.which("podman")

    def _inspect_image(self, runtime: str) -> tuple[ImageInspection | None, str | None]:
        command = [runtime, "image", "inspect", "--format", "{{json .}}", self.config.image_ref]
        try:
            completed = _run(command, timeout_seconds=min(self.config.timeout_seconds, 30))
        except subprocess.TimeoutExpired:
            return None, "provider_image_inspect_timeout"
        except OSError:
            return None, "provider_image_inspect_failed"
        if completed.returncode != 0:
            return None, "provider_image_unavailable"
        try:
            payload: object = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None, "provider_image_metadata_malformed"
        if not isinstance(payload, Mapping):
            return None, "provider_image_metadata_malformed"
        image_id = payload.get("Id")
        config = payload.get("Config")
        if (
            not isinstance(image_id, str)
            or not image_id.startswith("sha256:")
            or not isinstance(config, Mapping)
        ):
            return None, "provider_image_metadata_malformed"
        raw_labels = config.get("Labels")
        if not isinstance(raw_labels, Mapping):
            return None, "provider_image_labels_missing"
        labels = {str(key): str(value) for key, value in raw_labels.items()}
        expected = {
            LABEL_REVISION: self.config.provider_revision,
            LABEL_DEPENDENCIES: self.config.dependency_set_digest,
            LABEL_MODE: self.config.scan_mode,
            LABEL_POLICY: self.config.policy_config_revision,
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            return None, "provider_image_manifest_mismatch"
        if self.config.expected_image_id is not None and image_id != self.config.expected_image_id:
            return None, "provider_image_identity_mismatch"
        return ImageInspection(image_id, labels), None

    def _normalize(
        self,
        candidate: StagedSkillCandidate,
        image_id: str,
        observed_at: datetime,
        returncode: int,
        report: Mapping[str, object] | None,
        raw_digest: str | None,
        raw_ref: str | None,
        extra_reasons: tuple[str, ...],
    ) -> SecurityEvidence:
        reasons = list(extra_reasons)
        findings: tuple[SecurityFinding, ...] = ()
        suppressions: tuple[Mapping[str, JsonValue], ...] = ()
        baseline: dict[str, JsonValue] = {}
        metadata: dict[str, JsonValue] = self._build_metadata(returncode)
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
            values = _finding_values(report)
            if values is None:
                complete = False
                reasons.append("provider_findings_missing")
            elif not all(isinstance(value, Mapping) for value in values):
                complete = False
                reasons.append("provider_findings_invalid")
            else:
                findings = tuple(
                    _normalize_finding(value) for value in values if isinstance(value, Mapping)
                )
            completeness = report.get("analysis_completeness")
            if not isinstance(completeness, Mapping):
                complete = False
                reasons.append("provider_analysis_completeness_missing")
            else:
                metadata["analysis_completeness"] = _json_mapping(completeness)
                if completeness.get("is_complete") is not True:
                    complete = False
                    reasons.append("provider_analysis_incomplete")
                if completeness.get("execution_successful") is False:
                    complete = False
                    reasons.append("provider_analysis_execution_failed")
                value = completeness.get("status")
                if isinstance(value, str) and value.lower() not in {
                    "complete",
                    "completed",
                    "full",
                }:
                    complete = False
                    reasons.append(f"provider_analysis_status={value.lower()}")
            for source_key, target_key in (
                ("risk_assessment", "risk_assessment"),
                ("metadata", "metadata"),
                ("skill", "skill"),
            ):
                value = report.get(source_key)
                if isinstance(value, Mapping):
                    metadata[target_key] = _json_mapping(value)
            value = report.get("baseline")
            if isinstance(value, Mapping):
                baseline = _json_mapping(value)
            value = report.get("suppressed")
            if isinstance(value, list):
                suppressions = tuple(
                    _json_mapping(item) for item in value if isinstance(item, Mapping)
                )
        if raw_digest is None:
            complete = False
            reasons.append("raw_report_not_retained")
        status = (
            SecurityEvidenceStatus.DEGRADED
            if not complete
            else SecurityEvidenceStatus.FINDINGS
            if findings
            else SecurityEvidenceStatus.CLEAN
        )
        return SecurityEvidence(
            evidence_id=new_security_evidence_id(),
            provider=PROVIDER_ID,
            provider_version=self.config.provider_version,
            provider_revision=self.config.provider_revision,
            provider_build_identity=image_id,
            dependency_set_digest=self.config.dependency_set_digest,
            scan_mode=self.config.scan_mode,
            policy_config_revision=self.config.policy_config_revision,
            observed_at=_report_time(report, observed_at),
            candidate_id=candidate.candidate_id,
            candidate_revision=candidate.candidate_revision,
            candidate_digest=candidate.candidate_digest,
            status=status,
            complete=complete,
            findings=findings,
            degraded_reasons=tuple(dict.fromkeys(reasons)),
            suppression_metadata=suppressions,
            baseline_metadata=baseline,
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
            provider_metadata=metadata,
            known_provider_limitations=KNOWN_LIMITATIONS,
            raw_report_digest=raw_digest,
            raw_report_artifact_ref=raw_ref,
        )

    def _build_metadata(self, returncode: int) -> dict[str, JsonValue]:
        return {
            "scanner_exit_code": returncode,
            "provider_native_recommendation_is_advisory": True,
            "evaluated_image_id": EVALUATED_IMAGE_ID,
            "upstream_dockerfile_sha256": PINNED_DOCKERFILE_SHA256,
            "production_dockerfile_sha256": PRODUCTION_DOCKERFILE_SHA256,
            "production_requirements_sha256": PRODUCTION_REQUIREMENTS_SHA256,
            "pinned_license_sha256": PINNED_LICENSE_SHA256,
            "evaluation_artifact_sha256": EVALUATION_ARTIFACT_SHA256,
        }

    def _degraded(
        self,
        candidate: StagedSkillCandidate,
        observed_at: datetime,
        reason: str,
        image_id: str = "unavailable",
    ) -> SecurityEvidence:
        return SecurityEvidence(
            evidence_id=new_security_evidence_id(),
            provider=PROVIDER_ID,
            provider_version=self.config.provider_version,
            provider_revision=self.config.provider_revision,
            provider_build_identity=image_id,
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
            provider_metadata=self._build_metadata(-1),
            known_provider_limitations=KNOWN_LIMITATIONS,
        )


def build_container_command(
    runtime: str,
    config: SkillSpectorConfig,
    input_dir: Path,
    output_dir: Path,
) -> list[str]:
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
    return {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}


def digest_tree(root: Path) -> str:
    digest = sha256()
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_candidate(source: Path, expected_digest: str) -> Path:
    if source.is_symlink():
        raise ValueError("candidate root cannot be a symlink")
    resolved = source.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("candidate must be a local directory")
    for path in resolved.rglob("*"):
        if path.is_symlink():
            raise ValueError("candidate contains a symlink")
        path.resolve(strict=True).relative_to(resolved)
    if digest_tree(resolved) != expected_digest:
        raise ValueError("candidate digest mismatch")
    return resolved


def _prepare_output_directory(path: Path) -> None:
    path.mkdir()
    if os.name == "posix":
        path.chmod(0o733)


def _run(command: Sequence[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=sanitized_environment(),
    )
    if len(result.stdout.encode()) > MAX_CAPTURE_BYTES:
        result.stdout = result.stdout.encode()[:MAX_CAPTURE_BYTES].decode(errors="replace")
    if len(result.stderr.encode()) > MAX_CAPTURE_BYTES:
        result.stderr = result.stderr.encode()[:MAX_CAPTURE_BYTES].decode(errors="replace")
    return result


def _finding_values(report: Mapping[str, object]) -> list[object] | None:
    for key in ("issues", "findings", "results"):
        value = report.get(key)
        if isinstance(value, list):
            return value
    return None


def _normalize_finding(item: Mapping[object, object]) -> SecurityFinding:
    raw_location = item.get("location")
    location: Mapping[object, object] = raw_location if isinstance(raw_location, Mapping) else {}
    line = _opt_int(location.get("start_line") or location.get("line"))
    if line is None:
        line = _opt_int(item.get("start_line") or item.get("line") or item.get("line_number"))
    extras: dict[str, JsonValue] = {}
    for key in ("pattern", "remediation", "intent", "match_fingerprint", "tags", "evidence"):
        if key in item:
            extras[key] = _json_value(item[key])
    severity = _first(item, "severity", "risk_level", "level")
    return SecurityFinding(
        rule_id=_opt_str(_first(item, "id", "rule_id")),
        occurrence_id=_opt_str(_first(item, "finding_id", "provider_finding_id")),
        category=_opt_str(_first(item, "category", "type", "kind")),
        severity=str(severity).lower() if severity is not None else None,
        confidence=_opt_float(item.get("confidence")),
        summary=_opt_str(
            _first(
                item,
                "title",
                "summary",
                "message",
                "finding",
                "description",
                "explanation",
            )
        ),
        path=_opt_str(
            location.get("file") or location.get("path") or item.get("file") or item.get("path")
        ),
        line=line,
        metadata=extras,
    )


def _report_time(report: Mapping[str, object] | None, fallback: datetime) -> datetime:
    if report is not None:
        raw_skill = report.get("skill")
        if isinstance(raw_skill, Mapping):
            value = raw_skill.get("scanned_at")
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
