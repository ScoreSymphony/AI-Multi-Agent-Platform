from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.agents.runtime import AgentRuntime
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.evaluation.context import EvaluationExecutionContext
from ai_multi_agent_platform.evaluation.contracts import EvaluationCaseExecutor
from ai_multi_agent_platform.evaluation.models import (
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
)
from ai_multi_agent_platform.evaluation.product import (
    AgentTargetValidatingCaseExecutor,
    DirectoryEvaluationFixtureResolver,
)
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.portability.composition import (
    _capability_dependency_available,
)
from ai_multi_agent_platform.portability.models import DependencyKind, DependencyRequirement


class _ObservationExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return EvaluationObservation(run_id="run-evaluation-target")


class _AgentRepository:
    def __init__(self, record: object) -> None:
        self.record = record

    def list_agent_runs(self, run_id: str) -> tuple[object, ...]:
        assert run_id == "run-evaluation-target"
        return (self.record,)


class _AgentRuntime:
    def __init__(self, expected_spec: object) -> None:
        self.expected_spec = expected_spec
        self.capability_registry = object()

    def prepare_agent(self, **kwargs: object) -> object:
        assert kwargs["agent_id"] == "agent-evaluation"
        assert kwargs["revision"] == 2
        assert kwargs["requested_capability_ids"] == ("cap.requested",)
        return self.expected_spec


class _ModelRegistry:
    def get_model(self, model_config_id: str) -> object:
        assert model_config_id == "model-evaluation"
        return SimpleNamespace(provider_id="provider-evaluation")


def _target_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="target-case",
        name="Exact Agent target",
        version="1.0",
        input_template={
            "evaluation_target": {
                "kind": "agent",
                "agent_id": "agent-evaluation",
                "agent_revision": 2,
                "model_config_id": "model-evaluation",
                "capability_ids": ["cap.requested"],
            }
        },
    )


def _attempt(case: EvaluationCase) -> EvaluationAttempt:
    return EvaluationAttempt(
        evaluation_run_id="evaluation-run",
        case_id=case.case_id,
        case_version=case.version,
        repetition_index=0,
        attempt_id="evaluation-attempt",
    )


def _expected_spec() -> object:
    return SimpleNamespace(
        selected_model_config_id="model-evaluation",
        selected_provider_id="provider-evaluation",
        capability_ids=("cap.required", "cap.requested"),
        capability_versions={"cap.required": "1.0", "cap.requested": "2.0"},
    )


def _agent_record(
    *,
    capability_ids: tuple[str, ...],
    capability_versions: dict[str, str] | None = None,
) -> object:
    versions = capability_versions or {
        capability_id: {
            "cap.required": "1.0",
            "cap.requested": "2.0",
            "cap.extra": "3.0",
        }[capability_id]
        for capability_id in capability_ids
    }
    return SimpleNamespace(
        agent=SimpleNamespace(agent_id="agent-evaluation", revision=2),
        selected_model_config_id="model-evaluation",
        selected_provider_id="provider-evaluation",
        capability_ids=capability_ids,
        capability_versions=versions,
    )


def _validator(record: object) -> AgentTargetValidatingCaseExecutor:
    return AgentTargetValidatingCaseExecutor(
        cast(EvaluationCaseExecutor, _ObservationExecutor()),
        cast(AgentRepository, _AgentRepository(record)),
        cast(AgentRuntime, _AgentRuntime(_expected_spec())),
        cast(ModelRegistry, _ModelRegistry()),
    )


def test_agent_target_rejects_extra_effective_capability() -> None:
    case = _target_case()
    validator = _validator(
        _agent_record(capability_ids=("cap.extra", "cap.required", "cap.requested"))
    )

    async def scenario() -> None:
        with pytest.raises(ValueError, match="capability set differs"):
            await validator.execute_case(
                case=case,
                attempt=_attempt(case),
                execution_context=EvaluationExecutionContext(attempt_id="evaluation-attempt"),
            )

    asyncio.run(scenario())


def test_agent_target_rejects_capability_version_drift() -> None:
    case = _target_case()
    validator = _validator(
        _agent_record(
            capability_ids=("cap.required", "cap.requested"),
            capability_versions={"cap.required": "1.0", "cap.requested": "2.1"},
        )
    )

    async def scenario() -> None:
        with pytest.raises(ValueError, match="capability versions differ"):
            await validator.execute_case(
                case=case,
                attempt=_attempt(case),
                execution_context=EvaluationExecutionContext(attempt_id="evaluation-attempt"),
            )

    asyncio.run(scenario())


def test_agent_target_accepts_exact_server_resolved_capabilities_and_versions() -> None:
    case = _target_case()
    validator = _validator(_agent_record(capability_ids=("cap.required", "cap.requested")))

    async def scenario() -> None:
        observation = await validator.execute_case(
            case=case,
            attempt=_attempt(case),
            execution_context=EvaluationExecutionContext(attempt_id="evaluation-attempt"),
        )
        assert observation.run_id == "run-evaluation-target"

    asyncio.run(scenario())


def _fixture_resolver(root: Path) -> DirectoryEvaluationFixtureResolver:
    return DirectoryEvaluationFixtureResolver(
        fixture_root=root,
        files=cast(FileProvider, object()),
        project_id="project_00000000-0000-4000-8000-000000000019",
        owner_ref=OwnerRef(type="service", id="evaluation-tests"),
    )


def test_directory_fixture_lookup_rejects_parent_and_absolute_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    resolver = _fixture_resolver(root)

    assert resolver.fixture_exists("../outside") is False
    assert resolver.fixture_exists(str(outside.resolve())) is False


def test_directory_fixture_materialization_rejects_symlink_file_escape(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are not supported on this platform")

    root = tmp_path / "fixtures"
    fixture_dir = root / "safe-fixture"
    fixture_dir.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("host secret\n", encoding="utf-8")
    link = fixture_dir / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("test environment does not permit symlink creation")

    resolver = _fixture_resolver(root)
    case = EvaluationCase(
        case_id="fixture-case",
        name="Fixture confinement",
        version="1.0",
        fixtures=("safe-fixture",),
    )

    async def scenario() -> None:
        with pytest.raises(ValueError, match="outside its configured directory"):
            await resolver.resolve_fixtures(case=case, attempt=_attempt(case))

    asyncio.run(scenario())


class _CapabilityRegistry:
    def inventory_capabilities(self) -> tuple[object, ...]:
        return (
            SimpleNamespace(
                capability_id="capability.test",
                required_permissions=(),
                required_worker_capabilities=(),
            ),
        )

    def resolve(
        self,
        capability_id: str,
        *,
        version: str | None = None,
        compatibility: object | None = None,
        granted_permissions: frozenset[str] | None = None,
        available_worker_capabilities: frozenset[str] | None = None,
    ) -> tuple[object, object]:
        del granted_permissions, available_worker_capabilities
        assert capability_id == "capability.test"
        assert version == "2.0"
        assert compatibility is None
        return object(), object()


def test_portability_recognizes_existing_capability_dependency() -> None:
    requirement = DependencyRequirement(
        kind=DependencyKind.CAPABILITY,
        identifier="capability.test",
        version_constraint="==2.0",
    )

    assert _capability_dependency_available(
        cast(CapabilityRegistry, _CapabilityRegistry()),
        requirement,
    )
