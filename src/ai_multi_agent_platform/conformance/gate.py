"""Platform-wide conformance runner for the M3 operational-v1 gate.

The runner aggregates evidence owned by canonical subsystems. It intentionally does
not implement a second platform stack. Required scenarios that do not yet have a
registered acceptance path remain explicit ``not_implemented`` results, while
optional capabilities may be reported as ``disabled`` or ``unsupported`` without
invalidating the reference single-node baseline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic

REPORT_SCHEMA = "ai-multi-agent-platform/platform-conformance/v1"
_PACKAGE_NAME = "ai-multi-agent-platform"


class ConformanceProfile(StrEnum):
    FAST = "fast"
    INTEGRATION = "integration"
    RELEASE = "release"


class ConformanceStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"
    NOT_IMPLEMENTED = "not_implemented"


class CompatibilityResult(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    INCOMPLETE = "incomplete"
    NOT_CLAIMED = "not_claimed"


@dataclass(frozen=True, slots=True)
class ComponentVersion:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class ConformanceScenario:
    scenario_id: str
    owner: str
    criterion: str
    command: tuple[str, ...] | None
    required: bool = True
    unavailable_status: ConformanceStatus = ConformanceStatus.NOT_IMPLEMENTED
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConformanceScenarioResult:
    scenario_id: str
    owner: str
    criterion: str
    required: bool
    status: str
    compatibility_result: str
    duration_seconds: float
    command: tuple[str, ...]
    stdout: str
    stderr: str
    failure_category: str | None
    reason: str | None
    canonical_resource_ids: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    schema: str
    profile: str
    deployment_profile: str
    platform_commit: str | None
    platform_release: str | None
    passed: bool
    compatibility_result: str
    adapter_versions: tuple[ComponentVersion, ...]
    provider_versions: tuple[ComponentVersion, ...]
    plugin_versions: tuple[ComponentVersion, ...]
    scenarios: tuple[ConformanceScenarioResult, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    def human_summary(self) -> str:
        headline = (
            f"platform conformance [{self.profile}/{self.deployment_profile}]: "
            f"{'PASS' if self.passed else 'FAIL'} ({self.compatibility_result})"
        )
        lines = [headline]
        for result in self.scenarios:
            requirement = "required" if result.required else "optional"
            lines.append(
                f"- {result.status.upper()} {result.scenario_id} "
                f"({requirement}; {result.owner}) — {result.criterion}"
            )
            if result.reason:
                lines.append(f"  reason: {result.reason}")
            if result.status == ConformanceStatus.FAIL.value:
                if result.stdout:
                    lines.append("  stdout: " + _single_line(result.stdout))
                if result.stderr:
                    lines.append("  stderr: " + _single_line(result.stderr))
        return "\n".join(lines)


def _pytest(*nodes: str) -> tuple[str, ...]:
    return (sys.executable, "-m", "pytest", "-q", *nodes)


def _python_module(module: str, *args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", module, *args)


def _optional(
    scenario_id: str,
    owner: str,
    criterion: str,
    reason: str,
    *,
    status: ConformanceStatus = ConformanceStatus.DISABLED,
) -> ConformanceScenario:
    return ConformanceScenario(
        scenario_id=scenario_id,
        owner=owner,
        criterion=criterion,
        command=None,
        required=False,
        unavailable_status=status,
        unavailable_reason=reason,
    )


def _pending(scenario_id: str, owner: str, criterion: str) -> ConformanceScenario:
    return ConformanceScenario(
        scenario_id=scenario_id,
        owner=owner,
        criterion=criterion,
        command=None,
        required=True,
        unavailable_status=ConformanceStatus.NOT_IMPLEMENTED,
        unavailable_reason="the #46 end-to-end acceptance path is not registered yet",
    )


def _fast_scenarios() -> tuple[ConformanceScenario, ...]:
    return (
        ConformanceScenario(
            "A",
            "#39/#252 reference baseline",
            (
                "reference-only authenticated Task/Run/Result execution remains retry-safe "
                "across service reconstruction"
            ),
            _pytest(
                "tests/test_issue39_single_node_deployment.py::"
                "test_single_node_reference_smoke_is_retry_safe_across_restart"
            ),
        ),
        ConformanceScenario(
            "D-model",
            "#10/#250/#252 local model",
            "local/self-hosted model invocation works without a paid external service",
            _pytest(
                "tests/test_issue_252_acceptance_gate.py::"
                "test_local_ai_profile_uses_real_loopback_openai_compatible_endpoint"
            ),
        ),
        ConformanceScenario(
            "D-capability",
            "#12 capability boundary",
            "capability discovery and invocation remain contract-driven and replaceable",
            _pytest("tests/test_issue_12_reopen.py"),
        ),
        ConformanceScenario(
            "D-vertical",
            "#46/#10/#12 local model + capability",
            (
                "one authenticated AgentRun crosses a real loopback local-model HTTP boundary "
                "and executes its pinned native capability through CapabilityInvoker"
            ),
            _pytest(
                "tests/test_issue_46_local_model_native_capability_e2e.py::"
                "test_authenticated_local_model_executes_native_capability_end_to_end"
            ),
        ),
        ConformanceScenario(
            "F",
            "#15 authorization/approval",
            "approval is exact-action bound and changed-payload reuse is rejected",
            _pytest("tests/test_issue_15_final_boundaries.py"),
        ),
        ConformanceScenario(
            "H",
            "#39/#250/#251/#86 persistence",
            "canonical Task/Run/Memory/Verification state remains coherent across restart",
            _python_module(
                "ai_multi_agent_platform.cli.acceptance",
                "--profile",
                "persistence",
            ),
        ),
        ConformanceScenario(
            "J-cli",
            "#17/#252 CLI client",
            "CLI reads canonical Task state through the versioned Control Plane resource path",
            _pytest(
                "tests/test_issue_252_acceptance_gate.py::"
                "test_cli_and_web_share_canonical_task_fixture_and_route"
            ),
        ),
        ConformanceScenario(
            "J-web",
            "#17/#395 Web client",
            "Web reads the same canonical Task fixture through the same versioned API path",
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
        ConformanceScenario(
            "U",
            "#86 runtime verification",
            "required canonical Verification independently gates concrete completion",
            _pytest("tests/test_issue_86_kernel_gate.py"),
        ),
        ConformanceScenario(
            "ARCH",
            "#46 architecture invariants",
            "canonical core remains independent from optional backend implementations",
            _pytest("tests/test_issue_46_architecture_invariants.py"),
        ),
    )


def profile_scenarios(profile: ConformanceProfile) -> tuple[ConformanceScenario, ...]:
    fast = _fast_scenarios()
    if profile is ConformanceProfile.FAST:
        return fast

    integration = fast + (
        _optional(
            "B",
            "#8 Hermes adapter",
            "Hermes orchestration maps through canonical contracts with a non-Hermes executor",
            "Hermes integration profile is not enabled by the reference conformance run",
        ),
        _optional(
            "C",
            "#9 Forge adapter",
            "Forge executes behind the canonical Executor boundary without lifecycle authority",
            "Forge integration profile is not enabled by the reference conformance run",
        ),
        _optional(
            "E",
            "#14 distributed Worker",
            "a second Worker/Node preserves canonical IDs, authorization and trace context",
            "distributed Worker profile is optional and not enabled",
        ),
        _optional(
            "S",
            "#81 optional Registry",
            "Registry-disabled baseline works and a local catalog can be validated when enabled",
            "Registry is optional and disabled",
        ),
        _optional(
            "X",
            "#89 optional Control Plane HA",
            "HA failover fences stale authority without changing single-node semantics",
            "HA is optional and disabled",
        ),
    )
    if profile is ConformanceProfile.INTEGRATION:
        return integration

    return integration + (
        ConformanceScenario(
            "REL-BACKUP",
            "#40 backup/restore",
            (
                "replacement-machine restore preserves canonical identity/history and reaches "
                "service readiness"
            ),
            _pytest(
                "tests/test_issue40_replacement_machine.py::"
                "test_clean_replacement_machine_restore_preserves_canonical_history"
            ),
        ),
        ConformanceScenario(
            "REL-UPGRADE",
            "#41 upgrade lifecycle",
            (
                "supported schema upgrade uses preflight, recorded migrations, backup semantics "
                "and explicit recovery"
            ),
            _pytest(
                "tests/test_issue41_upgrade_lifecycle.py::"
                "test_upgrade_from_previous_schema_fixture_records_history",
                "tests/test_issue41_upgrade_lifecycle.py::"
                "test_forward_only_migration_requires_matching_verified_backup",
                "tests/test_issue41_upgrade_lifecycle.py::"
                "test_failed_upgrade_stays_in_maintenance_until_explicit_resume",
            ),
        ),
        ConformanceScenario(
            "REL-EVAL",
            "#19 evaluation/regression",
            (
                "checked-in deterministic evaluation baseline rejects regressions "
                "without paid services"
            ),
            (sys.executable, "scripts/ci/issue19_evaluation_gate.py"),
        ),
        ConformanceScenario(
            "REL-VERTICAL",
            "#46 authenticated full reference vertical slice",
            (
                "one authenticated canonical Task/Run crosses Agent/Model, Capability/Tool, "
                "Executor/Worker/Node, Workspace/File/Artifact, Verification and returns through "
                "canonical API/timeline/observability without shadow lifecycle state"
            ),
            _pytest(
                "tests/test_issue_46_worker_artifact_verification_vertical.py::"
                "test_authenticated_worker_artifact_is_exact_verification_evidence_same_run"
            ),
        ),
        ConformanceScenario(
            "G",
            "#46 failure/retry",
            "controlled failures preserve canonical retries and telemetry",
            _pytest(
                "tests/test_issue_46_failure_retry_e2e.py::"
                "test_controlled_failure_retry_preserves_canonical_history_and_retry_telemetry"
            ),
        ),
        ConformanceScenario(
            "I",
            "#18 automation",
            "automation creates a normal canonical Task lifecycle",
            _pytest(
                "tests/test_automation.py::"
                "test_one_time_schedule_creates_canonical_task_with_provenance"
            ),
        ),
        ConformanceScenario(
            "K",
            "#72 Chat",
            "Chat creates durable canonical work without becoming lifecycle truth",
            _pytest(
                "tests/test_issue_72_control_plane.py::"
                "test_message_to_task_handoff_is_canonical_and_bidirectionally_linked"
            ),
        ),
        ConformanceScenario(
            "L",
            "#73 Terminal",
            "terminal/session access remains authorized and Workspace-bounded",
            _pytest(
                "tests/test_issue73_control_plane_e2e.py::"
                "test_terminal_http_resource_and_command_use_standard_composition_and_"
                "idempotent_create"
            ),
        ),
        ConformanceScenario(
            "M",
            "#74 Browser",
            "browser work uses replaceable Capability/File/security boundaries",
            _pytest(
                "tests/test_browser_capability.py::"
                "test_download_enters_canonical_file_and_artifact_path_with_redacted_provenance",
                "tests/test_browser_capability.py::"
                "test_form_side_effect_is_policy_gated_and_upload_reads_authorized_canonical_file",
            ),
        ),
        _optional(
            "N",
            "#75 Notifications",
            "notifications remain scoped, deduplicated and source-linked",
            "notification integration profile is optional and not enabled",
        ),
        ConformanceScenario(
            "O",
            "#76 Usage/resources",
            "usage remains attributable through canonical IDs",
            _pytest(
                "tests/test_issue76_accounting.py::"
                "test_task_run_executor_accounting_is_idempotent_and_aggregated"
            ),
        ),
        ConformanceScenario(
            "P",
            "#77 Standard Agents/Teams",
            "bundled Agent/Team definitions remain editable configuration rather than architecture",
            _pytest(
                "tests/test_issue_77_completion_hardening.py::"
                "test_standard_catalog_lifecycle_uses_real_control_plane_http_command_path"
            ),
        ),
        _optional(
            "Q",
            "#78 Templates",
            "template preview/instantiate preserves permissions and immutable instance intent",
            "Template conformance profile is optional and not enabled",
        ),
        _optional(
            "R",
            "#79 Import/export",
            (
                "portable round-trip preserves references/checksums while excluding "
                "secrets/runtime state"
            ),
            "portable import/export conformance profile is optional and not enabled",
        ),
        _optional(
            "T",
            "#82 Repository/Git",
            "exact Git revision provenance remains canonical-Workspace bounded",
            "Repository/Git conformance profile is optional and not enabled",
        ),
        _optional(
            "V",
            "#87 Organizations/Teams",
            "organization isolation and membership revocation preserve historical provenance",
            "organization collaboration conformance profile is optional and not enabled",
        ),
        ConformanceScenario(
            "W",
            "#88 Task management",
            "priority/deadline/assignment/dependencies remain metadata over canonical lifecycle",
            _pytest(
                "tests/test_task_management.py::"
                "test_priority_deadline_not_before_and_query_projection",
                "tests/test_task_management.py::"
                "test_responsibility_reassignment_and_agent_assignment_are_permission_neutral",
                "tests/test_task_management.py::"
                "test_dependency_satisfaction_cycle_cross_project_and_blocked_reason",
            ),
        ),
        _optional(
            "Y",
            "#384 durable Plan/Step coordination",
            "durable fan-out/fan-in, waits, retries and cancellation advance exactly once",
            "durable Plan/Step coordination profile is optional and not enabled",
        ),
    )


def run_conformance(
    profile: ConformanceProfile,
    *,
    repository_root: Path | None = None,
    deployment_profile: str | None = None,
    adapter_versions: Mapping[str, str] | None = None,
    provider_versions: Mapping[str, str] | None = None,
    plugin_versions: Mapping[str, str] | None = None,
    scenarios: Sequence[ConformanceScenario] | None = None,
) -> ConformanceReport:
    root = (repository_root or Path.cwd()).resolve()
    selected = tuple(scenarios) if scenarios is not None else profile_scenarios(profile)
    results = tuple(_run_scenario(scenario, root=root) for scenario in selected)
    passed = all(
        result.status == ConformanceStatus.PASS.value
        if result.required
        else result.status != ConformanceStatus.FAIL.value
        for result in results
    )
    return ConformanceReport(
        schema=REPORT_SCHEMA,
        profile=profile.value,
        deployment_profile=deployment_profile or profile.value,
        platform_commit=_git_commit(root),
        platform_release=_package_version(),
        passed=passed,
        compatibility_result=_report_compatibility(results).value,
        adapter_versions=_component_versions(adapter_versions),
        provider_versions=_component_versions(provider_versions),
        plugin_versions=_component_versions(plugin_versions),
        scenarios=results,
    )


def _run_scenario(scenario: ConformanceScenario, *, root: Path) -> ConformanceScenarioResult:
    if scenario.command is None:
        status = scenario.unavailable_status
        return ConformanceScenarioResult(
            scenario_id=scenario.scenario_id,
            owner=scenario.owner,
            criterion=scenario.criterion,
            required=scenario.required,
            status=status.value,
            compatibility_result=CompatibilityResult.NOT_CLAIMED.value,
            duration_seconds=0.0,
            command=(),
            stdout="",
            stderr="",
            failure_category=status.value,
            reason=scenario.unavailable_reason,
            canonical_resource_ids=(),
            evidence=(),
        )

    started = monotonic()
    try:
        process = subprocess.run(
            scenario.command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return ConformanceScenarioResult(
            scenario_id=scenario.scenario_id,
            owner=scenario.owner,
            criterion=scenario.criterion,
            required=scenario.required,
            status=ConformanceStatus.FAIL.value,
            compatibility_result=CompatibilityResult.INCOMPATIBLE.value,
            duration_seconds=round(monotonic() - started, 3),
            command=scenario.command,
            stdout="",
            stderr=str(exc),
            failure_category="command_unavailable",
            reason="the registered conformance command could not be executed",
            canonical_resource_ids=(),
            evidence=(),
        )

    passed = process.returncode == 0
    return ConformanceScenarioResult(
        scenario_id=scenario.scenario_id,
        owner=scenario.owner,
        criterion=scenario.criterion,
        required=scenario.required,
        status=(ConformanceStatus.PASS if passed else ConformanceStatus.FAIL).value,
        compatibility_result=(
            CompatibilityResult.COMPATIBLE if passed else CompatibilityResult.INCOMPATIBLE
        ).value,
        duration_seconds=round(monotonic() - started, 3),
        command=scenario.command,
        stdout=_tail(process.stdout),
        stderr=_tail(process.stderr),
        failure_category=None if passed else "acceptance_failure",
        reason=None if passed else f"registered command exited with status {process.returncode}",
        canonical_resource_ids=(),
        evidence=("registered-command",),
    )


def _report_compatibility(
    results: Sequence[ConformanceScenarioResult],
) -> CompatibilityResult:
    if any(result.status == ConformanceStatus.FAIL.value for result in results):
        return CompatibilityResult.INCOMPATIBLE
    if any(result.required and result.status != ConformanceStatus.PASS.value for result in results):
        return CompatibilityResult.INCOMPLETE
    return CompatibilityResult.COMPATIBLE


def _component_versions(values: Mapping[str, str] | None) -> tuple[ComponentVersion, ...]:
    return tuple(
        ComponentVersion(name=name, version=component_version)
        for name, component_version in sorted((values or {}).items())
    )


def _git_commit(root: Path) -> str | None:
    try:
        process = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    value = process.stdout.strip()
    return value if process.returncode == 0 and value else None


def _package_version() -> str | None:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


def _tail(value: str, *, limit: int = 4000) -> str:
    value = value.strip()
    return value[-limit:]


def _single_line(value: str, *, limit: int = 500) -> str:
    return " ".join(value.split())[-limit:]
