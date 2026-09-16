from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

PINNED_VERSION = "2.11.2"
PINNED_REVISION = "69dcdfb74487d361ba4c811d088cfdea2ff3a9dc"
PINNED_DEPENDENCY_SET_SHA256 = "d6716d890040ac73494a8422ea51ef11229e71e0c0d9b538a95316a3a644fc61"
SCAN_MODE = "static_no_llm_network_none"
POLICY_CONFIG_REVISION = "skillspector-static-v1"
KNOWN_LIMITATIONS = (
    "This integration uses SkillSpector's static analysis path only; LLM-assisted modes are disabled.",
    "Container execution is network-isolated and scans an explicitly mounted workspace snapshot.",
    "Provider rule coverage depends on the pinned SkillSpector release and is not treated as a complete security proof.",
)


@dataclass(frozen=True)
class SkillSpectorFinding:
    rule_id: str
    title: str
    severity: str
    category: str
    file_path: str | None
    line: int | None
    message: str


@dataclass(frozen=True)
class SkillSpectorEvidence:
    provider_version: str
    provider_revision: str
    dependency_set_digest: str
    scan_mode: str
    policy_config_revision: str
    image_id: str
    workspace_digest: str
    provider_complete: bool
    findings: tuple[SkillSpectorFinding, ...]
    degraded_reasons: tuple[str, ...]
    known_provider_limitations: tuple[str, ...]
    raw_report_digest: str
    raw_report_artifact_ref: str | None = None


@dataclass(frozen=True)
class SkillSpectorConfig:
    image: str = f"skillspector-production:{PINNED_VERSION}"
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
            raise ValueError("unsupported SkillSpector pin; rerun corpus before upgrade")
        if self.dependency_set_digest != PINNED_DEPENDENCY_SET_SHA256:
            raise ValueError("SkillSpector dependency lock does not match approved production pin")
        if self.scan_mode != SCAN_MODE:
            raise ValueError("only static_no_llm_network_none is supported")
        if self.policy_config_revision != POLICY_CONFIG_REVISION:
            raise ValueError("unsupported SkillSpector policy/config revision")
        if self.expected_image_id is not None and not self.expected_image_id.startswith("sha256:"):
            raise ValueError("expected_image_id must be an OCI sha256 image ID")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.pids_limit < 1:
            raise ValueError("pids_limit must be positive")


class SkillSpectorAdapter:
    def __init__(
        self,
        *,
        config: SkillSpectorConfig | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._config = config or SkillSpectorConfig()
        self._runner = runner

    @property
    def config(self) -> SkillSpectorConfig:
        return self._config

    def scan_workspace(
        self,
        workspace: Path,
        *,
        raw_report_artifact_ref: str | None = None,
    ) -> SkillSpectorEvidence:
        root = workspace.resolve()
        if not root.is_dir():
            raise ValueError("workspace must exist and be a directory")

        workspace_digest = _workspace_digest(root)
        image_id = self._resolve_image_id()
        command = self._build_command(root)
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ContractError(
                ErrorCode.BACKEND_TIMEOUT,
                "SkillSpector scan timed out",
                retryable=True,
                details={"provider": "skillspector"},
            ) from exc
        except OSError as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "SkillSpector execution failed",
                retryable=False,
                details={"provider": "skillspector"},
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "SkillSpector scan failed",
                retryable=False,
                details={
                    "provider": "skillspector",
                    "exit_code": completed.returncode,
                    "stderr_digest": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
                },
            )

        try:
            report = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "SkillSpector returned invalid JSON",
                retryable=False,
                details={"provider": "skillspector"},
            ) from exc
        if not isinstance(report, Mapping):
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "SkillSpector returned an invalid report shape",
                retryable=False,
                details={"provider": "skillspector"},
            )

        raw_report = _canonical_json(report)
        findings, provider_complete, degraded_reasons = _normalize_report(report)
        return SkillSpectorEvidence(
            provider_version=self._config.provider_version,
            provider_revision=self._config.provider_revision,
            dependency_set_digest=self._config.dependency_set_digest,
            scan_mode=self._config.scan_mode,
            policy_config_revision=self._config.policy_config_revision,
            image_id=image_id,
            workspace_digest=workspace_digest,
            provider_complete=provider_complete,
            findings=findings,
            degraded_reasons=degraded_reasons,
            known_provider_limitations=KNOWN_LIMITATIONS,
            raw_report_digest=hashlib.sha256(raw_report.encode("utf-8")).hexdigest(),
            raw_report_artifact_ref=raw_report_artifact_ref,
        )

    def _resolve_image_id(self) -> str:
        if self._config.expected_image_id is not None:
            return self._config.expected_image_id
        try:
            completed = self._runner(
                ["docker", "image", "inspect", self._config.image, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "SkillSpector image is unavailable",
                retryable=False,
                details={"provider": "skillspector"},
            ) from exc
        if completed.returncode != 0:
            raise ContractError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "SkillSpector image is unavailable",
                retryable=False,
                details={"provider": "skillspector"},
            )
        image_id = (completed.stdout or "").strip()
        if not image_id.startswith("sha256:"):
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "SkillSpector image identity is invalid",
                retryable=False,
                details={"provider": "skillspector"},
            )
        return image_id

    def _build_command(self, workspace: Path) -> list[str]:
        mount = f"{workspace}:/scan/workspace:ro"
        return [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--memory={self._config.memory_limit}",
            f"--cpus={self._config.cpu_limit}",
            f"--pids-limit={self._config.pids_limit}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={self._config.tmpfs_size}",
            "--volume",
            mount,
            self._config.image,
            "scan",
            "/scan/workspace",
            "--format",
            "json",
            "--no-llm",
        ]


def _workspace_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize_report(
    report: Mapping[str, Any],
) -> tuple[tuple[SkillSpectorFinding, ...], bool, tuple[str, ...]]:
    findings_value = report.get("findings", ())
    if not isinstance(findings_value, Sequence) or isinstance(findings_value, (str, bytes)):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "SkillSpector findings are invalid",
            retryable=False,
            details={"provider": "skillspector"},
        )

    findings: list[SkillSpectorFinding] = []
    for item in findings_value:
        if not isinstance(item, Mapping):
            continue
        finding = _normalize_finding(item)
        if finding is not None:
            findings.append(finding)
    findings.sort(
        key=lambda finding: (
            finding.severity,
            finding.rule_id,
            finding.file_path or "",
            finding.line or 0,
            finding.message,
        )
    )

    provider_complete = bool(report.get("complete", True))
    degraded_reasons = _normalize_string_tuple(report.get("degraded_reasons", ()))
    if not provider_complete and not degraded_reasons:
        degraded_reasons = ("provider_analysis_incomplete",)
    return tuple(findings), provider_complete, degraded_reasons


def _normalize_finding(item: Mapping[str, Any]) -> SkillSpectorFinding | None:
    rule_id = _string_or_empty(item.get("rule_id") or item.get("rule") or item.get("id"))
    title = _string_or_empty(item.get("title") or item.get("name") or rule_id)
    severity = _normalize_severity(item.get("severity"))
    category = _string_or_empty(item.get("category") or item.get("kind") or "security")
    message = _string_or_empty(item.get("message") or item.get("description") or title)
    file_path = _optional_string(item.get("file") or item.get("path"))
    line = _optional_positive_int(item.get("line"))
    if not rule_id and not message:
        return None
    return SkillSpectorFinding(
        rule_id=rule_id or "skillspector.unknown",
        title=title or message,
        severity=severity,
        category=category or "security",
        file_path=file_path,
        line=line,
        message=message,
    )


def _normalize_severity(value: object) -> str:
    normalized = _string_or_empty(value).lower()
    aliases = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "moderate": "medium",
        "low": "low",
        "info": "info",
        "informational": "info",
    }
    return aliases.get(normalized, "unknown")


def _normalize_string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Sequence):
        return ()
    normalized = tuple(item for item in (_string_or_empty(entry) for entry in value) if item)
    return normalized


def _string_or_empty(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_string(value: object) -> str | None:
    normalized = _string_or_empty(value)
    return normalized or None


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return int(value)
    return None


__all__ = [
    "KNOWN_LIMITATIONS",
    "PINNED_DEPENDENCY_SET_SHA256",
    "PINNED_REVISION",
    "PINNED_VERSION",
    "POLICY_CONFIG_REVISION",
    "SCAN_MODE",
    "SkillSpectorAdapter",
    "SkillSpectorConfig",
    "SkillSpectorEvidence",
    "SkillSpectorFinding",
]
