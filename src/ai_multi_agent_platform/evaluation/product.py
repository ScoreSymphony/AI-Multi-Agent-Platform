"""Product-facing Evaluation assets, targets, snapshot enrichment and fixtures.

Issue #19 keeps the canonical evaluator/runtime contracts generic. This module turns
those contracts into deployable configuration without depending on portability or a
registry/marketplace: suites and policies are explicit files under one deployment
asset directory, agent targets bind to the normal Agent execution metadata contract,
and fixture directories become canonical Workspace file evidence before each attempt.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.agents.runtime import AgentRuntime
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileRecord
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelRegistry, ModelRuntime, RoutingRequirements
from ai_multi_agent_platform.workspaces import (
    WorkspaceFile,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
)

from .aggregation import AggregationPolicy, ResultAggregator
from .aggregation_config import load_aggregation_policy
from .config import load_evaluation_suite, load_regression_policy
from .context import EvaluationExecutionContext
from .contracts import EvaluationCaseExecutor, EvaluationHistoryRepository
from .hardening import merge_snapshot_references
from .model_judge import ModelJudgeEvaluator
from .models import (
    ConfigurationSnapshot,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationSuite,
    RegressionPolicy,
    VersionReference,
)
from .regression import RegressionEngine
from .runner import EvaluationRunner, EvaluationRunSummary
from .service import EvaluationService
from .workspace import EvaluationFixtureResolver, ResolvedEvaluationFixtures

EVALUATION_TARGET_KEY = "evaluation_target"
_EVALUATION_PREFLIGHT_TASK_ID = "task_00000000-0000-4000-8000-000000000019"
_EVALUATION_PREFLIGHT_RUN_ID = "run_00000000-0000-4000-8000-000000000019"


@dataclass(frozen=True, slots=True)
class AgentEvaluationTarget:
    """Exact Agent execution target declared by one versioned EvaluationCase."""

    agent_id: str
    agent_revision: int
    model_config_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    snapshot_references: tuple[VersionReference, ...] = ()

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("evaluation target agent_id must not be blank")
        if self.agent_revision <= 0:
            raise ValueError("evaluation target agent_revision must be greater than zero")
        if self.model_config_id is not None and not self.model_config_id.strip():
            raise ValueError("evaluation target model_config_id must not be blank")
        if any(not capability.strip() for capability in self.capability_ids):
            raise ValueError("evaluation target capability_ids must not be blank")
        if len(self.capability_ids) != len(set(self.capability_ids)):
            raise ValueError("evaluation target capability_ids must be unique")
        identities = [(item.kind, item.ref_id) for item in self.snapshot_references]
        if len(identities) != len(set(identities)):
            raise ValueError("evaluation target snapshot references must be unique")


@dataclass(frozen=True, slots=True)
class EvaluationModelJudgeConfiguration:
    model_config_id: str
    configuration_ref: str
    evaluator_id: str = "reference.model-judge"
    version: str = "1.0"

    def __post_init__(self) -> None:
        for label, value in (
            ("model_config_id", self.model_config_id),
            ("configuration_ref", self.configuration_ref),
            ("evaluator_id", self.evaluator_id),
            ("version", self.version),
        ):
            if not value.strip():
                raise ValueError(f"evaluation model judge {label} must not be blank")

    def build(self, runtime: ModelRuntime) -> ModelJudgeEvaluator:
        return ModelJudgeEvaluator(
            runtime=runtime,
            model_config_id=self.model_config_id,
            configuration_ref=self.configuration_ref,
            evaluator_id=self.evaluator_id,
            version=self.version,
        )


@dataclass(frozen=True, slots=True)
class EvaluationAssetBundle:
    suites: tuple[EvaluationSuite, ...] = ()
    regression_policies: tuple[RegressionPolicy, ...] = ()
    aggregation_policies: tuple[AggregationPolicy, ...] = ()
    model_judge: EvaluationModelJudgeConfiguration | None = None


def parse_agent_evaluation_target(case: EvaluationCase) -> AgentEvaluationTarget | None:
    """Parse the reserved product target from an otherwise generic case input template."""

    raw = case.input_template.get(EVALUATION_TARGET_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"{EVALUATION_TARGET_KEY} must be an object")
    kind = raw.get("kind")
    if kind != "agent":
        raise ValueError("evaluation_target.kind must be 'agent'")
    agent_id = _required_string(raw, "agent_id", "evaluation_target")
    revision = _required_positive_int(raw, "agent_revision", "evaluation_target")
    model_config_id = _optional_string(raw, "model_config_id", "evaluation_target")
    capability_ids = _string_tuple(
        raw.get("capability_ids", []), "evaluation_target.capability_ids"
    )
    raw_references = raw.get("snapshot_references", [])
    if not isinstance(raw_references, list):
        raise ValueError("evaluation_target.snapshot_references must be an array")
    references: list[VersionReference] = []
    for index, value in enumerate(raw_references):
        if not isinstance(value, Mapping):
            raise ValueError(f"evaluation_target.snapshot_references[{index}] must be an object")
        context = f"evaluation_target.snapshot_references[{index}]"
        references.append(
            VersionReference(
                kind=_required_string(value, "kind", context),
                ref_id=_required_string(value, "ref_id", context),
                version=_required_string(value, "version", context),
                revision=_optional_string(value, "revision", context),
            )
        )
    return AgentEvaluationTarget(
        agent_id=agent_id,
        agent_revision=revision,
        model_config_id=model_config_id,
        capability_ids=capability_ids,
        snapshot_references=tuple(references),
    )


def evaluation_task_metadata(
    case: EvaluationCase,
    execution_context: EvaluationExecutionContext,
) -> dict[str, JsonValue]:
    """Bind a case target to the normal canonical Agent execution Task metadata."""

    target = parse_agent_evaluation_target(case)
    if target is None:
        return {}
    return encode_agent_execution_binding(
        AgentExecutionBinding(
            agent_id=target.agent_id,
            agent_revision=target.agent_revision,
            model_config_id=target.model_config_id,
            capability_ids=target.capability_ids,
            workspace_id=execution_context.workspace_id,
        )
    )


class AgentTargetValidatingCaseExecutor:
    """Fail closed when runtime Agent/model/capability identity differs from the target."""

    def __init__(
        self,
        executor: EvaluationCaseExecutor,
        agents: AgentRepository,
        models: ModelRegistry,
    ) -> None:
        self._executor = executor
        self._agents = agents
        self._models = models

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        observation = await self._executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=execution_context,
        )
        target = parse_agent_evaluation_target(case)
        if target is None:
            return observation
        if observation.run_id is None:
            raise ValueError("agent-target evaluation did not produce a canonical Run ID")
        records = self._agents.list_agent_runs(observation.run_id)
        if len(records) != 1:
            raise ValueError(
                "agent-target evaluation requires exactly one canonical AgentRun for the Run"
            )
        record = records[0]
        if (record.agent.agent_id, record.agent.revision) != (
            target.agent_id,
            target.agent_revision,
        ):
            raise ValueError("evaluated Agent identity differs from the declared target")
        if (
            target.model_config_id is not None
            and record.selected_model_config_id != target.model_config_id
        ):
            raise ValueError("evaluated model configuration differs from the declared target")
        if record.selected_model_config_id is None:
            raise ValueError(
                "agent-target evaluation did not record a selected model configuration"
            )
        model = self._models.get_model(record.selected_model_config_id)
        if record.selected_provider_id != model.provider_id:
            raise ValueError(
                "AgentRun provider evidence conflicts with the selected model configuration"
            )
        missing_capabilities = set(target.capability_ids) - set(record.capability_ids)
        if missing_capabilities:
            raise ValueError(
                "evaluated AgentRun is missing declared target capabilities: "
                + ", ".join(sorted(missing_capabilities))
            )
        declared_versions = {
            reference.ref_id: reference.version
            for reference in target.snapshot_references
            if reference.kind == "capability"
        }
        for capability_id in target.capability_ids:
            if capability_id not in declared_versions:
                continue
            actual_version = record.capability_versions.get(capability_id)
            if actual_version != declared_versions[capability_id]:
                raise ValueError(
                    "evaluated capability version differs from the declared target: "
                    f"{capability_id}"
                )
        return observation


class EvaluationTargetSnapshotEnricher:
    """Resolve target-owned runtime identity before execution and pin it into the snapshot."""

    def __init__(
        self,
        *,
        agents: AgentRuntime,
        models: ModelRegistry,
    ) -> None:
        self._agents = agents
        self._models = models

    def enrich(
        self,
        suite: EvaluationSuite,
        snapshot: ConfigurationSnapshot,
    ) -> ConfigurationSnapshot:
        references: list[VersionReference] = []
        for case in suite.cases:
            target = parse_agent_evaluation_target(case)
            if target is None:
                continue
            override = (
                None
                if target.model_config_id is None
                else RoutingRequirements(
                    explicit_model_id=target.model_config_id,
                    modalities=("text",),
                )
            )
            spec = self._agents.prepare_agent(
                task_id=_EVALUATION_PREFLIGHT_TASK_ID,
                run_id=_EVALUATION_PREFLIGHT_RUN_ID,
                agent_id=target.agent_id,
                revision=target.agent_revision,
                task_model_override=override,
                requested_capability_ids=target.capability_ids,
                available_capability_ids=(
                    frozenset(target.capability_ids)
                    if self._agents.capability_registry is None
                    else frozenset()
                ),
            )
            instructions = spec.agent_revision.profile.instructions
            prompt_config_payload = {
                "role": {
                    "content": instructions.role.content,
                    "ref": instructions.role.ref,
                    "version": instructions.role.version,
                },
                "platform_constraint_refs": list(instructions.platform_constraint_refs),
                "project_instruction_refs": list(instructions.project_instruction_refs),
            }
            prompt_config_digest = hashlib.sha256(
                json.dumps(
                    prompt_config_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            references.extend(
                (
                    VersionReference(
                        kind="agent",
                        ref_id=spec.agent_revision.agent_id,
                        version=str(spec.agent_revision.revision),
                    ),
                    VersionReference(
                        kind="prompt_config",
                        ref_id=f"{spec.agent_revision.agent_id}:instructions",
                        version=str(spec.agent_revision.revision),
                        revision=f"sha256:{prompt_config_digest}",
                    ),
                )
            )
            if spec.selected_model_config_id is None or spec.selected_provider_id is None:
                raise ValueError("agent-target preflight did not resolve model/provider identity")
            model = self._models.get_model(spec.selected_model_config_id)
            provider = self._models.get_provider(spec.selected_provider_id)
            references.extend(
                (
                    VersionReference(
                        kind="model",
                        ref_id=model.config_id,
                        version=str(model.revision),
                    ),
                    VersionReference(
                        kind="provider",
                        ref_id=provider.descriptor.provider_id,
                        version=provider.descriptor.contract_version,
                    ),
                )
            )
            for capability_id, version in sorted(spec.capability_versions.items()):
                references.append(
                    VersionReference(
                        kind="capability",
                        ref_id=capability_id,
                        version=version,
                    )
                )
            references.extend(target.snapshot_references)
        return merge_snapshot_references(snapshot, tuple(references))


class TargetAwareEvaluationService(EvaluationService):
    """EvaluationService that server-enriches exact target configuration before execution."""

    def __init__(
        self,
        *,
        repository: EvaluationHistoryRepository,
        runner: EvaluationRunner,
        suites: tuple[EvaluationSuite, ...],
        target_enricher: EvaluationTargetSnapshotEnricher,
        policies: tuple[RegressionPolicy, ...] = (),
        aggregation_policies: tuple[AggregationPolicy, ...] = (),
        regression_engine: RegressionEngine | None = None,
        result_aggregator: ResultAggregator | None = None,
    ) -> None:
        super().__init__(
            repository=repository,
            runner=runner,
            suites=suites,
            policies=policies,
            aggregation_policies=aggregation_policies,
            regression_engine=regression_engine,
            result_aggregator=result_aggregator,
        )
        self._target_enricher = target_enricher

    async def run_suite(
        self,
        *,
        suite_ref: str,
        snapshot: ConfigurationSnapshot,
        repetitions: int = 1,
        seed: int | None = None,
        baseline_run_id: str | None = None,
        regression_policy_ref_value: str | None = None,
        aggregation_policy_ref_value: str | None = None,
    ) -> EvaluationRunSummary:
        suite = self.get_suite(suite_ref)
        enriched = self._target_enricher.enrich(suite, snapshot)
        return await super().run_suite(
            suite_ref=suite_ref,
            snapshot=enriched,
            repetitions=repetitions,
            seed=seed,
            baseline_run_id=baseline_run_id,
            regression_policy_ref_value=regression_policy_ref_value,
            aggregation_policy_ref_value=aggregation_policy_ref_value,
        )


class DirectoryEvaluationFixtureResolver(EvaluationFixtureResolver):
    """Resolve explicit deployment fixture directories into canonical File evidence."""

    def __init__(
        self,
        *,
        fixture_root: str | Path,
        files: FileProvider,
        project_id: str,
        owner_ref: OwnerRef,
    ) -> None:
        self._root = Path(fixture_root)
        self._files = files
        self._project_id = project_id
        self._owner_ref = owner_ref

    async def resolve_fixtures(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> ResolvedEvaluationFixtures:
        workspace_files: list[WorkspaceFile] = []
        source_refs: list[WorkspaceSourceRef] = []
        for fixture_id in case.fixtures:
            fixture_dir = self._root / fixture_id
            if not fixture_dir.is_dir():
                raise ValueError(f"evaluation fixture directory not found: {fixture_id}")
            fixture_paths = tuple(sorted(path for path in fixture_dir.rglob("*") if path.is_file()))
            if not fixture_paths:
                raise ValueError(f"evaluation fixture directory is empty: {fixture_id}")
            digest = hashlib.sha256()
            for path in fixture_paths:
                relative_path = path.relative_to(fixture_dir).as_posix()
                data = path.read_bytes()
                file_digest = hashlib.sha256(data).hexdigest()
                digest.update(relative_path.encode("utf-8"))
                digest.update(b"\0")
                digest.update(file_digest.encode("ascii"))
                record = await self._canonical_file(
                    fixture_id=fixture_id,
                    relative_path=relative_path,
                    sha256=file_digest,
                    data=data,
                    attempt=attempt,
                    content_type=mimetypes.guess_type(relative_path)[0],
                )
                workspace_files.append(
                    WorkspaceFile(
                        relative_path=relative_path,
                        file_id=record.file_id,
                        sha256=record.sha256,
                    )
                )
            source_refs.append(
                WorkspaceSourceRef(
                    kind=WorkspaceSourceKind.FILES,
                    ref=f"evaluation-fixture:{fixture_id}",
                    revision=digest.hexdigest(),
                    checksum=digest.hexdigest(),
                )
            )
        relative_paths = [item.relative_path for item in workspace_files]
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError("evaluation fixtures contain duplicate workspace-relative paths")
        return ResolvedEvaluationFixtures(
            files=tuple(workspace_files),
            source_refs=tuple(source_refs),
        )

    async def _canonical_file(
        self,
        *,
        fixture_id: str,
        relative_path: str,
        sha256: str,
        data: bytes,
        attempt: EvaluationAttempt,
        content_type: str | None,
    ) -> FileRecord:
        context = DataAccessContext(
            operation=OperationContext(
                correlation_id=attempt.attempt_id,
                causation_id=attempt.evaluation_run_id,
                owner_type=self._owner_ref.type,
                owner_id=self._owner_ref.id,
                project_id=self._project_id,
            ),
            actor_ref=f"{self._owner_ref.type}:{self._owner_ref.id}",
        )
        for record in await self._files.list_files(context):
            if (
                record.sha256 == sha256
                and record.metadata.get("evaluation_fixture_id") == fixture_id
                and record.metadata.get("evaluation_fixture_path") == relative_path
            ):
                return record
        return await self._files.create_file(
            data,
            context,
            content_type=content_type,
            metadata={
                "evaluation_fixture_id": fixture_id,
                "evaluation_fixture_path": relative_path,
                "evaluation_fixture_sha256": sha256,
            },
        )


def load_evaluation_assets(root: str | Path) -> EvaluationAssetBundle:
    """Load strict deployment-owned Evaluation assets from conventional subdirectories."""

    base = Path(root)
    suites = tuple(
        load_evaluation_suite(path)
        for path in sorted((base / "suites").glob("*.json"))
        if path.is_file()
    )
    regression_policies = tuple(
        load_regression_policy(path)
        for path in sorted((base / "regression-policies").glob("*.json"))
        if path.is_file()
    )
    aggregation_policies = tuple(
        load_aggregation_policy(path)
        for path in sorted((base / "aggregation-policies").glob("*.json"))
        if path.is_file()
    )
    _require_unique(
        ((suite.suite_id, suite.version) for suite in suites),
        "evaluation suite",
    )
    _require_unique(
        ((policy.policy_id, policy.version) for policy in regression_policies),
        "evaluation regression policy",
    )
    _require_unique(
        ((policy.policy_id, policy.version) for policy in aggregation_policies),
        "evaluation aggregation policy",
    )
    judge_path = base / "model-judge.json"
    judge = _load_model_judge(judge_path) if judge_path.is_file() else None
    return EvaluationAssetBundle(
        suites=suites,
        regression_policies=regression_policies,
        aggregation_policies=aggregation_policies,
        model_judge=judge,
    )


def _load_model_judge(path: Path) -> EvaluationModelJudgeConfiguration:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"evaluation model judge must contain valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("evaluation model judge root must be an object")
    allowed = {"model_config_id", "configuration_ref", "evaluator_id", "version"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            "evaluation model judge contains unknown fields: " + ", ".join(sorted(unknown))
        )
    evaluator_id = raw.get("evaluator_id", "reference.model-judge")
    version = raw.get("version", "1.0")
    if not isinstance(evaluator_id, str) or not evaluator_id.strip():
        raise ValueError("evaluation model judge.evaluator_id must be a non-blank string")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("evaluation model judge.version must be a non-blank string")
    return EvaluationModelJudgeConfiguration(
        model_config_id=_required_string(raw, "model_config_id", "evaluation model judge"),
        configuration_ref=_required_string(raw, "configuration_ref", "evaluation model judge"),
        evaluator_id=evaluator_id,
        version=version,
    )


def _require_unique(values: Iterable[tuple[str, str]], label: str) -> None:
    pairs = list(values)
    if len(pairs) != len(set(pairs)):
        raise ValueError(f"duplicate configured {label} identity/version")


def _required_string(values: Mapping[str, object], key: str, context: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-blank string")
    return value


def _optional_string(values: Mapping[str, object], key: str, context: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-blank string or null")
    return value


def _required_positive_int(values: Mapping[str, object], key: str, context: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context}.{key} must be a positive integer")
    return value


def _string_tuple(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")
    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{context} must contain non-blank strings")
        parsed.append(item)
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{context} must contain unique values")
    return tuple(parsed)


__all__ = [
    "AgentEvaluationTarget",
    "AgentTargetValidatingCaseExecutor",
    "DirectoryEvaluationFixtureResolver",
    "EVALUATION_TARGET_KEY",
    "EvaluationAssetBundle",
    "EvaluationModelJudgeConfiguration",
    "EvaluationTargetSnapshotEnricher",
    "TargetAwareEvaluationService",
    "evaluation_task_metadata",
    "load_evaluation_assets",
    "parse_agent_evaluation_target",
]
