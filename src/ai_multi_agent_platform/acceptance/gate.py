"""Reusable acceptance runner for the single-node usable-prototype gate.

The gate deliberately reuses canonical integration evidence instead of creating a
second platform implementation. Each check names the owning subsystem and runs a
small, deterministic command from a repository checkout. Results can therefore be
consumed by CI as JSON while remaining understandable to a local operator.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from time import monotonic

REPORT_SCHEMA = "ai-multi-agent-platform/prototype-acceptance/v1"


class AcceptanceProfile(StrEnum):
    REFERENCE = "reference"
    LOCAL_AI = "local-ai"
    DEGRADED = "degraded"
    PERSISTENCE = "persistence"


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    check_id: str
    owner: str
    criterion: str
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceCheckResult:
    check_id: str
    owner: str
    criterion: str
    passed: bool
    exit_code: int
    duration_seconds: float
    command: tuple[str, ...]
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    schema: str
    profile: str
    passed: bool
    checks: tuple[AcceptanceCheckResult, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    def human_summary(self) -> str:
        headline = f"prototype acceptance [{self.profile}]: {'PASS' if self.passed else 'FAIL'}"
        lines = [headline]
        for check in self.checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"- {status} {check.check_id} ({check.owner}) — {check.criterion}")
            if not check.passed:
                if check.stdout:
                    lines.append("  stdout: " + _single_line(check.stdout))
                if check.stderr:
                    lines.append("  stderr: " + _single_line(check.stderr))
        return "\n".join(lines)


def _pytest(*nodes: str) -> tuple[str, ...]:
    return (sys.executable, "-m", "pytest", "-q", *nodes)


def profile_checks(profile: AcceptanceProfile) -> tuple[AcceptanceCheck, ...]:
    if profile is AcceptanceProfile.REFERENCE:
        return (
            AcceptanceCheck(
                "reference-smoke",
                "#39 single-node deployment",
                "authenticated canonical Task/Run/Result path and observable capability execution",
                _pytest(
                    "tests/test_issue39_single_node_deployment.py::"
                    "test_single_node_reference_smoke_is_retry_safe_across_restart"
                ),
            ),
            AcceptanceCheck(
                "approval-boundary",
                "#15 authorization/approval",
                "high-risk approval is exact-action bound and cannot authorize a modified action",
                _pytest("tests/test_issue_15_final_boundaries.py"),
            ),
            AcceptanceCheck(
                "verification-boundary",
                "#86 task verification",
                "verification remains canonical and independently gates concrete completion",
                _pytest("tests/test_issue_86_kernel_gate.py"),
            ),
            AcceptanceCheck(
                "memory-knowledge-lifecycle",
                "#251 data lifecycle",
                "Memory/Knowledge create, provenance/update and delete lifecycle remains canonical",
                _pytest("tests/test_issue_251_lifecycle_commands.py"),
            ),
            AcceptanceCheck(
                "memory-delete-not-found",
                "#251/#252 data acceptance",
                "Memory is retrieved with provenance, deleted, then unavailable as NOT_FOUND",
                _pytest(
                    "tests/test_issue_252_acceptance_gate.py::"
                    "test_memory_acceptance_create_retrieve_provenance_delete_not_found"
                ),
            ),
            AcceptanceCheck(
                "cli-canonical-state",
                "#17/#252 CLI client",
                "CLI reads the shared canonical Task fixture through the versioned Control Plane path",
                _pytest(
                    "tests/test_issue_252_acceptance_gate.py::"
                    "test_cli_and_web_share_canonical_task_fixture_and_route"
                ),
            ),
            AcceptanceCheck(
                "web-canonical-state",
                "#17/#395 Web client",
                "Web reads the same canonical Task fixture through the same versioned resource path",
                (
                    "npm",
                    "--prefix",
                    "frontend",
                    "test",
                    "--",
                    "--run",
                    "src/api/canonicalStateParity.test.ts",
                ),
            ),
        )
    if profile is AcceptanceProfile.LOCAL_AI:
        return (
            AcceptanceCheck(
                "loopback-openai-compatible",
                "#10/#250 model provider",
                (
                    "replaceable local/self-hosted HTTP model is discovered and generates "
                    "without paid service"
                ),
                _pytest(
                    "tests/test_issue_252_acceptance_gate.py::"
                    "test_local_ai_profile_uses_real_loopback_openai_compatible_endpoint"
                ),
            ),
            AcceptanceCheck(
                "first-run-local-model",
                "#250 first-run onboarding",
                (
                    "first-run local/self-hosted model configuration remains provider-neutral "
                    "and secret-safe"
                ),
                _pytest("tests/test_issue_250_first_run_onboarding.py"),
            ),
        )
    if profile is AcceptanceProfile.DEGRADED:
        return (
            AcceptanceCheck(
                "provider-inventory-revalidation",
                "#250 first-run onboarding",
                (
                    "missing configured provider-native model fails closed and can recover by "
                    "canonical health refresh"
                ),
                _pytest("tests/test_issue_250_restart_inventory_revalidation.py"),
            ),
            AcceptanceCheck(
                "readiness-hardening",
                "#250/#397 first-run readiness",
                "unavailable or non-executable optional paths never produce false readiness",
                _pytest("tests/test_issue_250_readiness_hardening.py"),
            ),
        )
    return (
        AcceptanceCheck(
            "canonical-restart-smoke",
            "#39 single-node deployment",
            "single-node canonical execution remains retry-safe across process reconstruction",
            _pytest(
                "tests/test_issue39_single_node_deployment.py::"
                "test_single_node_reference_smoke_is_retry_safe_across_restart"
            ),
        ),
        AcceptanceCheck(
            "memory-provider-restart",
            "#13/#251 data lifecycle",
            "user-owned Memory persists through provider reconstruction",
            _pytest("tests/test_data_reference.py::test_memory_persists_across_provider_restart"),
        ),
        AcceptanceCheck(
            "verification-restart",
            "#86 task verification",
            "canonical verification history persists through service reconstruction",
            _pytest("tests/test_issue_86_persistence.py"),
        ),
        AcceptanceCheck(
            "first-task-restart",
            "#250 first-run onboarding",
            "first-run Task/Run/Result state and explicit selection survive restart",
            _pytest("tests/test_issue_250_first_task_golden_path.py"),
        ),
    )


def run_acceptance(
    profile: AcceptanceProfile,
    *,
    repository_root: Path | None = None,
) -> AcceptanceReport:
    root = (repository_root or Path.cwd()).resolve()
    checks = profile_checks(profile)
    results: list[AcceptanceCheckResult] = []
    for check in checks:
        started = monotonic()
        process = subprocess.run(
            check.command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            AcceptanceCheckResult(
                check_id=check.check_id,
                owner=check.owner,
                criterion=check.criterion,
                passed=process.returncode == 0,
                exit_code=process.returncode,
                duration_seconds=round(monotonic() - started, 3),
                command=check.command,
                stdout=_tail(process.stdout),
                stderr=_tail(process.stderr),
            )
        )
    frozen_results = tuple(results)
    return AcceptanceReport(
        schema=REPORT_SCHEMA,
        profile=profile.value,
        passed=all(result.passed for result in frozen_results),
        checks=frozen_results,
    )


def _tail(value: str, *, limit: int = 4000) -> str:
    value = value.strip()
    return value[-limit:]


def _single_line(value: str, *, limit: int = 500) -> str:
    return " ".join(value.split())[-limit:]
