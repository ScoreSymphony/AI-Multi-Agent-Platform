"""Normalize SkillSpector output into evaluation-only platform evidence.

This module deliberately does not mutate Skill trust state.  It converts provider
output into a small, replaceable evidence envelope used by the #800 evaluation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedFinding:
    provider_id: str | None
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
    candidate_digest: str
    status: str
    complete: bool
    degraded_reasons: tuple[str, ...]
    findings: tuple[NormalizedFinding, ...]
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
    line = _first(location, "line", "start_line") or _first(item, "line", "line_number")
    try:
        normalized_line = int(line) if line is not None else None
    except (TypeError, ValueError):
        normalized_line = None
    return NormalizedFinding(
        provider_id=_first(item, "id", "rule_id", "finding_id"),
        category=_first(item, "category", "type", "kind"),
        severity=str(severity).lower() if severity is not None else None,
        confidence=normalized_confidence,
        summary=_first(item, "title", "summary", "message", "description"),
        path=_first(location, "path", "file") or _first(item, "path", "file"),
        line=normalized_line,
    )


def normalize_report(
    report: Mapping[str, Any],
    *,
    provider_version: str,
    provider_revision: str,
    mode: str,
    candidate_digest: str,
    process_ok: bool = True,
    degraded_reasons: tuple[str, ...] = (),
) -> SecurityEvidence:
    """Return immutable advisory evidence from a parsed SkillSpector JSON report.

    Scanner failure, malformed/partial output and explicit degradation can never
    be represented as a clean completed result.
    """
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    finding_values = report.get("findings")
    if not isinstance(finding_values, list):
        finding_values = report.get("results")
    if not isinstance(finding_values, list):
        finding_values = []

    reasons = list(degraded_reasons)
    complete = bool(process_ok)
    completeness = report.get("analysis_completeness")
    if completeness is not None and str(completeness).lower() not in {
        "complete",
        "completed",
        "full",
        "1",
        "true",
    }:
        complete = False
        reasons.append(f"provider_analysis_completeness={completeness}")
    if not process_ok:
        reasons.append("scanner_process_failed")

    findings = tuple(_normalize_finding(item) for item in finding_values)
    if not complete:
        status = "degraded"
    elif findings:
        status = "findings"
    else:
        status = "clean"

    provider_metadata = {
        key: report[key]
        for key in ("risk_score", "safe_to_install", "recommendation", "suppressed_count")
        if key in report
    }
    return SecurityEvidence(
        provider="nvidia/skillspector",
        provider_version=provider_version,
        provider_revision=provider_revision,
        mode=mode,
        candidate_digest=candidate_digest,
        status=status,
        complete=complete,
        degraded_reasons=tuple(dict.fromkeys(reasons)),
        findings=findings,
        provider_metadata=provider_metadata,
        raw_report_sha256=sha256(raw).hexdigest(),
    )
