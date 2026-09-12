"""Executable conformance gate for the canonical platform acceptance profiles (#46)."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from ai_multi_agent_platform.version import __version__

from .report import (
    ConformanceReport,
    ConformanceStatus,
    ProfileResult,
    ScenarioResult,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class ConformanceProfile(StrEnum):
    """Named acceptance profiles exposed by the gate."""

    FAST = "fast"
    OPERATIONAL = "operational"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class ConformanceScenario:
    """Executable scenario bound to one explicit acceptance criterion."""

    scenario_id: str
    owner: str
    criterion: str
    command: tuple[str, ...] | None
    skip_reason: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must not be blank")
        if not self.owner.strip():
            raise ValueError("owner must not be blank")
        if not self.criterion.strip():
            raise ValueError("criterion must not be blank")
        if self.command is None and self.skip_reason is None:
            raise ValueError("placeholder scenarios require a skip_reason")
        if self.command is not None and not self.command:
            raise ValueError("command must not be empty")
        if self.command is not None and self.skip_reason is not None:
            raise ValueError("executable scenarios cannot also declare a skip_reason")
        if not self.required and self.command is None and self.skip_reason is None:
            raise ValueError("optional scenarios require executable evidence or a skip reason")


@dataclass(frozen=True, slots=True)
class ScenarioExecution:
    """Normalized execution outcome injected into deterministic tests."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


ScenarioRunner = Callable[[ConformanceScenario], ScenarioExecution]


def _pytest(*node_ids: str) -> tuple[str, ...]:
    return (sys.executable, "-m", "pytest", "-q", *node_ids)


def _python_script(path: str, *arguments: str) -> tuple[str, ...]:
    return (sys.executable, path, *arguments)


def _script_and_pytest(
    script_path: str,
    *node_ids: str,
    script_arguments: tuple[str, ...] = (),
) -> tuple[str, ...]:
    script = " ".join(
        (
            json.dumps(sys.executable),
            json.dumps(script_path),
            *(json.dumps(argument) for argument in script_arguments),
        )
    )
    pytest = " ".join(
        (
            json.dumps(sys.executable),
            "-m pytest -q",
            *(json.dumps(node_id) for node_id in node_ids),
        )
    )
    return ("sh", "-c", f"{script} && {pytest}")


def _optional(
    scenario_id: str,
    owner: str,
    criterion: str,
    skip_reason: str,
    *,
    command: tuple[str, ...] | None = None,
) -> ConformanceScenario:
    return ConformanceScenario(
        scenario_id=scenario_id,
        owner=owner,
        criterion=criterion,
        command=command,
        skip_reason=None if command is not None else skip_reason,
        required=False,
    )


def _fast_scenarios() -> tuple[ConformanceScenario, ...]:
    return (
        ConformanceScenario(
            "D",
            "#5 Model Provider Layer",
            "retry/rate-limit/quota/provider-failure handling remains fail-closed",
            _pytest(
                "tests/test_issue_5_model_provider.py::test_provider_failures_are_mapped_to_canonical_error_categories",
                "tests/test_issue_5_model_provider.py::test_litellm_adapter_forwards_retries_and_normalizes_rate_limit",
                "tests/test_issue_5_model_provider.py::test_litellm_adapter_classifies_quota_exhaustion",
            ),
        ),
        ConformanceScenario(
            "F",
            "#9 Prompt Builder",
            "assembled prompts preserve source ordering, provenance and deterministic truncation",
            _pytest(
                "tests/test_issue_9_prompt_builder.py",
                "tests/test_issue_9_prompt_builder_enforcement.py",
            ),
        ),
        ConformanceScenario(
            "G",
            "#10 Data/File Layer",
            "workspace-scoped file CRUD and range access remain canonical and traversal-safe",
            _pytest("tests/test_issue_10_data_file_layer.py"),
        ),
        ConformanceScenario(
            "H",
            "#10 Data/File Layer",
            "artifact references survive checksum verification, retention and cleanup",
            _pytest("tests/test_issue_10_artifact_lifecycle.py"),
        ),
        ConformanceScenario(
            "I",
            "#15 Authorization + #23 RBAC",
            "forbidden actor-role combinations and approvals fail closed",
            _pytest(
                "tests/test_issue_15_authorization.py",
                "tests/test_issue_23_permission_enforcement_matrix.py",
                "tests/test_issue_25_approval_authority.py",
            ),
        ),
        ConformanceScenario(
            "J",
            "#16 Observability",
            "Task/Run/Agent/Provider/Capability telemetry propagates context without prompt capture",
            _pytest(
                "tests/test_issue_16_observability.py",
                "tests/test_issue_18_agent_model_observability.py",
                "tests/test_issue_16_workflow_timeline.py",
            ),
        ),
        ConformanceScenario(
            "K",
            "#17 Cost Accounting",
            "exact and estimated usage feed budget and project accounting with provenance",
            _pytest(
                "tests/test_issue_17_cost_accounting.py",
                "tests/test_issue_17_cost_accounting_integration.py",
            ),
        ),
        ConformanceScenario(
            "L",
            "#18 Reliability",
            "model/provider/capability failure paths preserve deterministic retry and terminal state",
            _pytest(
                "tests/test_issue_18_reliability.py",
                "tests/test_issue_18_recovery.py",
                "tests/test_issue_18_model_provider_reliability.py",
            ),
        ),
        ConformanceScenario(
            "M",
            "#19 Evaluation",
            "baseline comparison detects deterministic regression and missing evidence",
            _pytest(
                "tests/test_issue_19_evaluation.py",
                "tests/test_issue_19_regression_policy.py",
            ),
        ),
        ConformanceScenario(
            "O",
            "#47 MCP adapter baseline",
            "tool schema, invocation and canonical error mapping remain conformant",
            _pytest("tests/test_issue_47_mcp_adapter.py"),
        ),
        ConformanceScenario(
            "P",
            "#84/#85 Conversation execution",
            "conversation execution derives canonical Task/Run/AgentRun and persists assistant output",
            _pytest(
                "tests/test_issue_84_conversation_execution.py",
                "tests/test_issue_85_conversation_agent_invocation.py",
            ),
        ),
    )


def _operational_scenarios() -> tuple[ConformanceScenario, ...]:
    return (
        ConformanceScenario(
            "A",
            "#5/#6 Model routing",
            "fallback, explicit override, health and budget constraints select a canonical ModelConfig",
            _pytest(
                "tests/test_issue_6_routing.py",
                "tests/test_issue_6_router_constraints.py",
            ),
        ),
        _optional(
            "B",
            "#11 Connector Framework",
            "multiple connector backends cover auth, async jobs and provenance",
            "connector baseline is optional in the current operational profile",
        ),
        _optional(
            "C",
            "#13 Local Process Executor",
            "command execution obeys Workspace boundaries, timeout and lifecycle cleanup",
            "local process execution baseline is optional in the current operational profile",
        ),
        ConformanceScenario(
            "E",
            "#7 Capability + #8 Tool Layer",
            "tool invocation remains schema-validated, authorized and auditable",
            _pytest(
                "tests/test_issue_7_capabilities.py",
                "tests/test_issue_8_tools.py",
                "tests/test_issue_8_tool_execution.py",
            ),
        ),
        ConformanceScenario(
            "N",
            "#14 Distributed Runtime",
            "scheduler placement, reservations and worker loss preserve canonical ownership",
            _pytest(
                "tests/test_issue_14_distributed_runtime.py",
                "tests/test_issue_14_scheduler_recovery.py",
                "tests/test_issue_14_worker_job_cancellation.py",
            ),
        ),
        ConformanceScenario(
            "Q",
            "#37 Workspace Runtime",
            "canonical Workspace/Snapshot state survives isolated materialization and recovery",
            _pytest(
                "tests/test_issue_37_workspace_model.py",
                "tests/test_issue_37_workspace_service.py",
                "tests/test_issue_37_workspace_recovery.py",
            ),
        ),
        ConformanceScenario(
            "R",
            "#82 Repository Service",
            "repository refs, snapshots, diffs and side effects stay bound to exact canonical revisions",
            _pytest(
                "tests/test_issue_82_repository_service.py",
                "tests/test_issue_82_repository_runtime.py",
                "tests/test_issue_82_repository_provenance.py",
            ),
        ),
        ConformanceScenario(
            "S",
            "#33 Agent Runtime",
            "Agent/AgentTeam revisions and AgentRun evidence remain canonical and restart-safe",
            _pytest(
                "tests/test_issue_33_agents.py",
                "tests/test_issue_33_agent_runtime.py",
                "tests/test_issue_33_agent_run_recovery.py",
            ),
        ),
        ConformanceScenario(
            "T",
            "#86 Verification",
            "exact Result/Artifact subjects and reviewer evidence govern completion",
            _pytest(
                "tests/test_issue_86_verification_authority.py",
                "tests/test_issue_86_canonical_evidence.py",
                "tests/test_issue_86_reviewer_agent.py",
            ),
        ),
        ConformanceScenario(
            "U",
            "#15 Authorization side effects",
            "repository and runtime side effects cannot bypass canonical authorization/approval",
            _pytest(
                "tests/test_issue_15_authorization_enforcement.py",
                "tests/test_issue_82_repository_authorization.py",
            ),
        ),
        ConformanceScenario(
            "V",
            "#16 Operational observability",
            "worker, repository and verification paths remain traceable with canonical IDs",
            _pytest(
                "tests/test_issue_14_observability.py",
                "tests/test_issue_82_repository_observability.py",
                "tests/test_issue_86_verification_observability.py",
            ),
        ),
        ConformanceScenario(
            "X",
            "#18 Operational recovery",
            "restart reconciliation preserves canonical runtime evidence without duplicate effects",
            _pytest(
                "tests/test_issue_18_operational_recovery.py",
                "tests/test_issue_82_repository_recovery.py",
                "tests/test_issue_86_verification_recovery.py",
            ),
        ),
    )


def _release_scenarios() -> tuple[ConformanceScenario, ...]:
    return (
        ConformanceScenario(
            "REL-CI",
            "#46 CI",
            "the complete repository test suite is green",
            _pytest("tests"),
        ),
        ConformanceScenario(
            "REL-EVAL",
            "#19 Evaluation",
            "deterministic evaluation gate passes against the checked-in baseline",
            _python_script("scripts/ci/issue19_evaluation_gate.py"),
        ),
        ConformanceScenario(
            "REL-ASSETS",
            "#725 Runtime assets",
            "canonical runtime assets are materialized and in sync",
            _python_script("scripts/ci/issue725_materialize_runtime_assets.py", "--check"),
        ),
        ConformanceScenario(
            "REL-PACKAGE",
            "#46 Packaging",
            "the built wheel contains the canonical runtime assets and bootstrap entry points",
            _script_and_pytest(
                "scripts/ci/issue725_materialize_runtime_assets.py",
                "tests/test_issue_46_packaging_smoke.py",
                script_arguments=("--check",),
            ),
        ),
        ConformanceScenario(
            "REL-VERTICAL",
            "#46 Vertical slice",
            "a real single-node Task/Run/Agent/Workspace/Artifact/Verification path succeeds",
            _pytest(
                "tests/test_issue_46_full_stack_vertical.py",
                "tests/test_issue_46_worker_artifact_verification_vertical.py",
            ),
        ),
        ConformanceScenario(
            "REL-SECURITY",
            "#15/#23 Security",
            "release authorization, permission and approval matrices remain fail-closed",
            _pytest(
                "tests/test_issue_15_authorization.py",
                "tests/test_issue_15_authorization_enforcement.py",
                "tests/test_issue_23_permission_enforcement_matrix.py",
                "tests/test_issue_25_approval_authority.py",
            ),
        ),
        ConformanceScenario(
            "REL-RECOVERY",
            "#18 Recovery",
            "restart and recovery suites preserve canonical state without duplicate effects",
            _pytest(
                "tests/test_issue_18_recovery.py",
                "tests/test_issue_18_operational_recovery.py",
                "tests/test_issue_82_repository_recovery.py",
                "tests/test_issue_86_verification_recovery.py",
            ),
        ),
        ConformanceScenario(
            "W",
            "#46 Cross-domain acceptance",
            "canonical cross-domain workflows compose without parallel state models",
            _pytest(
                "tests/test_issue_46_cross_domain.py",
                "tests/test_issue_46_control_plane_resources.py",
            ),
        ),
        _optional(
            "Y",
            "#384 durable Plan/Step coordination",
            "durable fan-out/fan-in, waits, retries and cancellation advance exactly once",
            "durable Plan/Step coordination profile is optional and not enabled",
        ),
        ConformanceScenario(
            "Z",
            "#872/#46 parallel coding integration",
            (
                "two independent coding Steps fan out concurrently, a dependent Step waits, "
                "isolated workstreams integrate only after exact validation/authorization, and "
                "conflicts require a bounded canonical repair with fresh validation"
            ),
            _pytest(
                "tests/test_issue_46_parallel_coding_batch_e2e.py::test_parallel_coding_batch_uses_384_fanout_fanin_and_authorized_merge",
                "tests/test_issue_46_parallel_coding_batch_e2e.py::test_conflicting_valid_workstreams_require_canonical_repair_and_fresh_combined_validation",
            ),
        ),
    )


def profile_scenarios(profile: ConformanceProfile) -> tuple[ConformanceScenario, ...]:
    """Return the evidence-backed scenario registry for one profile."""

    fast = _fast_scenarios()
    if profile is ConformanceProfile.FAST:
        return fast
    operational = (*fast, *_operational_scenarios())
    if profile is ConformanceProfile.OPERATIONAL:
        return operational
    return (*operational, *_release_scenarios())


def profile_criteria(profile: ConformanceProfile) -> tuple[str, ...]:
    """Return human-readable criteria for docs/API surfaces."""

    return tuple(
        f"{scenario.scenario_id}: {scenario.criterion}" for scenario in profile_scenarios(profile)
    )


def run_conformance(
    profile: ConformanceProfile,
    *,
    runner: ScenarioRunner | None = None,
    environment: Mapping[str, str] | None = None,
    adapter_versions: Mapping[str, str | None] | None = None,
    provider_versions: Mapping[str, str | None] | None = None,
) -> ConformanceReport:
    """Execute a profile and return a normalized machine-readable report."""

    scenarios = profile_scenarios(profile)
    effective_runner = runner or _subprocess_runner
    results: list[ScenarioResult] = []
    started_at = datetime.now(UTC)
    for scenario in scenarios:
        results.append(_execute_scenario(scenario, effective_runner))
    finished_at = datetime.now(UTC)

    profile_result = ProfileResult(
        profile=profile.value,
        status=_profile_status(results),
        started_at=started_at,
        finished_at=finished_at,
        scenarios=tuple(results),
    )
    return ConformanceReport(
        schema="ai-multi-agent-platform.conformance/v1",
        generated_at=finished_at,
        platform_version=__version__,
        python_version=platform.python_version(),
        operating_system=platform.platform(),
        environment=dict(environment or {}),
        adapter_versions=_component_versions(adapter_versions),
        provider_versions=_component_versions(provider_versions),
        profile=profile_result,
    )


def _component_versions(
    versions: Mapping[str, str | None] | None,
) -> dict[str, str | None]:
    return {key: value for key, value in sorted((versions or {}).items())}


def _profile_status(results: Iterable[ScenarioResult]) -> ConformanceStatus:
    statuses = tuple(result.status for result in results)
    if any(status is ConformanceStatus.FAILED for status in statuses):
        return ConformanceStatus.FAILED
    if statuses and all(status is ConformanceStatus.SKIPPED for status in statuses):
        return ConformanceStatus.SKIPPED
    return ConformanceStatus.PASSED


def _execute_scenario(
    scenario: ConformanceScenario,
    runner: ScenarioRunner,
) -> ScenarioResult:
    started_at = datetime.now(UTC)
    if scenario.command is None:
        finished_at = datetime.now(UTC)
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            owner=scenario.owner,
            criterion=scenario.criterion,
            required=scenario.required,
            status=(ConformanceStatus.FAILED if scenario.required else ConformanceStatus.SKIPPED),
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=max(0.0, (finished_at - started_at).total_seconds()),
            command=(),
            skip_reason=scenario.skip_reason,
            exit_code=None,
            stdout="",
            stderr="",
        )

    execution = runner(scenario)
    finished_at = datetime.now(UTC)
    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        owner=scenario.owner,
        criterion=scenario.criterion,
        required=scenario.required,
        status=(ConformanceStatus.PASSED if execution.exit_code == 0 else ConformanceStatus.FAILED),
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=execution.duration_seconds,
        command=scenario.command,
        skip_reason=None,
        exit_code=execution.exit_code,
        stdout=execution.stdout,
        stderr=execution.stderr,
    )


def _subprocess_runner(scenario: ConformanceScenario) -> ScenarioExecution:
    if scenario.command is None:
        raise ValueError("cannot execute placeholder scenario")
    started_at = datetime.now(UTC)
    completed = subprocess.run(
        scenario.command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    finished_at = datetime.now(UTC)
    return ScenarioExecution(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=max(0.0, (finished_at - started_at).total_seconds()),
    )


def report_to_json(report: ConformanceReport) -> str:
    """Serialize the report using stable JSON for artifacts and CI logs."""

    return json.dumps(report.to_dict(), sort_keys=True, indent=2)


def report_from_json(payload: str) -> ConformanceReport:
    """Parse a stored conformance artifact without executing tests."""

    from .report import conformance_report_from_dict

    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("conformance report payload must be an object")
    return conformance_report_from_dict(raw)


def supported_profiles() -> Sequence[ConformanceProfile]:
    return tuple(ConformanceProfile)
