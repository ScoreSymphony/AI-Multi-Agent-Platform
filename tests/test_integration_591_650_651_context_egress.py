from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import AgentCapabilityTurn
from ai_multi_agent_platform.capabilities import CapabilityInvoker, CapabilityRegistry
from ai_multi_agent_platform.context.lifecycle import _bind_task_project_scope
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    OperationContext,
    digest_egress_payload,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import (
    CanonicalModelResponse,
    ModelContentBlock,
    ModelContentKind,
)
from ai_multi_agent_platform.security import EgressGate, InMemoryEgressAuditSink


class _CapturingModelRuntime:
    def __init__(self) -> None:
        self.request = None

    async def generate_canonical(self, request):
        self.request = request
        return CanonicalModelResponse(
            request_id=request.request_id,
            model_config_id=request.model_config_id or "model-test",
            content=(ModelContentBlock(ModelContentKind.TEXT, text="ok"),),
        )


def test_public_single_node_shares_one_durable_egress_gate(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "shared-egress", secure_cookie=False)
    )

    gate = deployment.egress.runtime.gate
    assert deployment.model_runtime.egress_gate is gate
    assert deployment.connectors.egress_gate is gate
    assert deployment.context.runtime.exporter.egress_gate is gate

    capability_turn = deployment.context.lifecycle._capability_turn  # noqa: SLF001
    assert capability_turn is not None
    invoker = capability_turn._invoker  # noqa: SLF001
    assert invoker.egress_gate is gate
    assert invoker._classification_resolver is not None  # noqa: SLF001
    assert "egress-profiles" in deployment.control_plane.registered_collections


def test_context_scope_binds_missing_project_and_rejects_conflicts() -> None:
    canonical_project = new_id("project")
    conflicting_project = new_id("project")
    base = OperationContext(
        correlation_id="context-scope-591-650",
        owner_type="user",
        owner_id="user-context-scope",
    )

    bound = _bind_task_project_scope(base, canonical_project)
    assert bound.project_id == canonical_project

    with pytest.raises(ContractError) as captured:
        _bind_task_project_scope(
            OperationContext(
                correlation_id="context-scope-conflict-591-650",
                owner_type="user",
                owner_id="user-context-scope",
                project_id=conflicting_project,
            ),
            canonical_project,
        )
    assert captured.value.code is ErrorCode.NOT_FOUND


def test_egress_audit_preserves_project_scope() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        sink = InMemoryEgressAuditSink()
        gate = EgressGate(audit_sink=sink)
        request = EgressRequest(
            request_id="egress-project-audit-591",
            target=EgressTarget(
                kind=EgressTargetKind.CONTEXT_EXPORT,
                target_id="internal-context-target",
                posture=EgressTargetPosture.INTERNAL,
            ),
            context=OperationContext(
                correlation_id="egress-project-audit-591",
                owner_type="user",
                owner_id="user-egress-audit",
                project_id=project_id,
            ),
            classification=DataClassification.INTERNAL,
            resource_type="context_bundle",
            payload_digest=digest_egress_payload({"context_bundle_id": "bundle-test"}),
        )

        decision = await gate.evaluate(request)
        assert decision.allowed
        assert len(sink.events) == 2
        assert all(event.project_id == project_id for event in sink.events)

    asyncio.run(scenario())


def test_agent_capability_turn_propagates_context_classification_to_model_request() -> None:
    async def scenario() -> None:
        models = _CapturingModelRuntime()
        registry = CapabilityRegistry()
        turn = AgentCapabilityTurn(
            models,  # type: ignore[arg-type]
            registry,
            CapabilityInvoker(registry),
        )
        await turn.execute(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            model_config_id="model-context-classification",
            instruction="Use canonical Context only.",
            objective="Return a short result.",
            capability_ids=(),
            capability_versions={},
            context=OperationContext(correlation_id="context-classification-turn"),
            data_classification=DataClassification.RESTRICTED,
        )

        assert models.request is not None
        assert models.request.routing_requirements["data_classification"] == "restricted"

    asyncio.run(scenario())
