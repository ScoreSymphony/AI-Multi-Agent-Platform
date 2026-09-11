"""Normalize SkillSpector output into evaluation-only platform evidence.

This module deliberately does not mutate Skill trust state. It converts provider
output into a small, replaceable evidence envelope used by the #800 evaluation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedFinding:
    provider_id: str | None
    rule_id: str | None
    category: str | None
    severity: str | None
    confidence: float | None
    summary: str | None
    path: str | None
    line: int | None


@dataclass(frozen=True)
class SecurityEvidence:
    provider: str
    provider_version: str
    provider_revision: str
    mode: str
    policy_config_version: str
    observed_at: str
    candidate_id: str
    candidate_revision: str
    candidate_digest: str
    status: str
    complete: bool
    degraded_reasons: tuple[str, ...]
    findings: tuple[NormalizedFinding, ...]
    suppressed_findings: tuple[Mapping[str, Any], ...]
    network_usage: Mapping[str, Any]
    provider_usage: Mapping[str, Any]
    provider_metadata: Mapping[str, Any]
    raw_report_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _normalize_finding(value: Any) -> NormalizedFinding:
    item = value if isinstance(value, Mapping) else {}
    location = item.get("location") if isinstance(item.get("location"), Mapping) else {}
    severity = _first(item, "severity", "risk_level", "level")
    confidence = item.get("confidence")
    try:
        normalized_confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        normalized_confidence = None
    line = _first(location, "start_line", "line")
    if line is None:
        line = _first(item, "start_line", "line", "line_number")
    try:
        normalized_line = int(line) if line is not None else None
    except (TypeError, ValueError):
        normalized_line = None
    return NormalizedFinding(
        provider_id=_first(item, "finding_id", "provider_finding_id"),
        rule_id=_first(item, "id", "rule_id"),
        category=_first(item, "category", "type", "kind"),
        severity=str(severity).lower() if severity is not None else None,
        confidence=normalized_confidence,
        summary=_first(item, "title", "summary", "message", "description", "finding", "explanation"),
        path=_first(location, "file", "path") or _first(item, "file", "path"),
        line=normalized_line,
    )


def _finding_values(report: Mapping[str, Any]) -> list[Any] | None:
    """Return findings from the pinned public JSON shape, with fallback aliases."""
    for key in ("issues", "findings", "results"):
        value = report.get(key)
        if isinstance(value, list):
            return value
    return None


def _suppressed_values(report: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    values = report.get("suppressed")
    if not isinstance(values, list):
        return ()
    return tuple(dict(item) for item in values if isinstance(item, Mapping))


def _observed_at(report: Mapping[str, Any], override: str | None) -> str:
    if override:
        return override
    skill = report.get("skill")
    if isinstance(skill, Mapping):
        scanned_at = skill.get("scanned_at")
        if isinstance(scanned_at, str) and scanned_at:
            return scanned_at
    return datetime.now(UTC).isoformat()


def _provider_metadata(report: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for mapping_key in ("risk_assessment", "analysis_completeness", "metadata"):
        value = report.get(mapping_key)
        if isinstance(value, Mapping):
            metadata[mapping_key] = dict(value)
    skill = report.get("skill")
    if isinstance(skill, Mapping):
        metadata["skill"] = dict(skill)
    for key in (
        "risk_score",
        "risk_severity",
        "risk_recommendation",
        "safe_to_install",
        "recommendation",
        "suppressed_count",
    ):
        if key in report:
            metadata[key] = report[key]
    return metadata


def normalize_report(
    report: Mapping[str, Any],
    *,
    provider_version: str,
    provider_revision: str,
    mode: str,
    policy_config_version: str,
    candidate_id: str,
    candidate_revision: str,
    candidate_digest: str,
    network_usage: Mapping[str, Any],
    provider_usage: Mapping[str, Any],
    observed_at: str | None = None,
    raw_report_sha256: str | None = None,
    process_ok: bool = True,
    degraded_reasons: tuple[str, ...] = (),
) -> SecurityEvidence:
    """Return immutable advisory evidence from a parsed SkillSpector JSON report.

    Scanner failure, malformed/partial output and explicit degradation can never
    be represented as a clean completed result. Provider risk recommendations
    remain metadata and never become platform trust state.
    """
    canonical_raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report_digest = raw_report_sha256 or sha256(canonical_raw).hexdigest()
    reasons = list(degraded_reasons)
    complete = bool(process_ok)

    execution_successful = report.get("execution_successful")
    if execution_successful is not True:
        complete = False
        reasons.append(
            "provider_execution_failed"
            if execution_successful is False
            else "provider_execution_status_missing"
        )

    completeness = report.get("analysis_completeness")
    if isinstance(completeness, Mapping):
        if completeness.get("is_complete") is not True:
            complete = False
            reasons.append("provider_analysis_incomplete")
        if completeness.get("execution_successful") is False:
            complete = False
            reasons.append("provider_analysis_execution_failed")
        status = completeness.get("status")
        if isinstance(status, str) and status.lower() in {"partial", "failed"}:
            complete = False
            reasons.append(f"provider_analysis_status={status.lower()}")
    elif completeness is None:
        complete = False
        reasons.append("provider_analysis_completeness_missing")
    elif str(completeness).lower() not in {
        "complete",
        "completed",
        "full",
        "1",
        "true",
    }:
        complete = False
        reasons.append(f"provider_analysis_completeness={completeness}")

    finding_values = _finding_values(report)
    if finding_values is None:
        complete = False
        reasons.append("provider_findings_missing")
        finding_values = []

    if not process_ok:
        reasons.append("scanner_process_failed")

    findings = tuple(_normalize_finding(item) for item in finding_values)
    if not complete:
        evidence_status = "degraded"
    elif findings:
        evidence_status = "findings"
    else:
        evidence_status = "clean"

    return SecurityEvidence(
        provider="nvidia/skillspector",
        provider_version=provider_version,
        provider_revision=provider_revision,
        mode=mode,
        policy_config_version=policy_config_version,
        observed_at=_observed_at(report, observed_at),
        candidate_id=candidate_id,
        candidate_revision=candidate_revision,
        candidate_digest=candidate_digest,
        status=evidence_status,
        complete=complete,
        degraded_reasons=tuple(dict.fromkeys(reasons)),
        findings=findings,
        suppressed_findings=_suppressed_values(report),
        network_usage=dict(network_usage),
        provider_usage=dict(provider_usage),
        provider_metadata=_provider_metadata(report),
        raw_report_sha256=report_digest,
    )
