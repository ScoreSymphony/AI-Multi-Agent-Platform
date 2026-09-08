from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextBindingReconciler,
    ContextBlockerReason,
    ContextBudget,
    ContextBundleEgressExporter,
    ContextBundleResourceService,
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextOmissionReason,
    ContextResolutionError,
    ContextResolver,
    ContextRoutingPolicy,
    ContextSourceAdapterBinding,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
    InMemoryContextBundleRepository,
    InMemoryContextRunBindingRepository,
    OperationalContextAssemblyService,
    context_window_requirement,
    merge_context_routing_requirements,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


class _UnavailableAdapter:
    adapter_id = "issue-650-unavailable-source/v1"

    async def collect(self, request):
        del request
        raise ContractError(ErrorCode.UNAVAILABLE, "canonical source provider unavailable")


class _HiddenVisibility:
    async def can_view(self, context, bundle, entry) -> bool:
        del context, bundle, entry
        return False


@dataclass(frozen=True)
class _AgentRef:
    agent_id: str
    revision: int


@dataclass(frozen=True)
class _PersistedAgentRun:
    agent_run_id: str
    run_id: str
    task_id: str
    agent: _AgentRef
    telemetry: dict[str, object]
    orchestrator_adapter_id: str
    started_at: datetime


class _AgentRunRepository:
    def __init__(self, records: tuple[_PersistedAgentRun, ...]) -> None:
        self.records = records

    def list_agent_runs(self):
        return self.records


def _candidate(
    source_type: ContextSourceType = ContextSourceType.TASK,
    *,
    content: str = "canonical issue 650 context",
    mandatory: bool = True,
) -> ContextCandidate:
    return ContextCandidate(
        source=ContextSourceRef(
            source_type=source_type,
            source_id=f"issue-650:{source_type.value}",
            revision="r1",
            digest=hashlib.sha256(f"source:{source_type.value}:r1".encode()).hexdigest(),
        ),
        role=(
            ContextEntryRole.INSTRUCTION
            if source_type is ContextSourceType.AGENT
            else ContextEntryRole.CONTEXT
        ),
        selection_reason="issue 650 operational regression",
        mandatory=mandatory,
        inline_content=content,
        trust=ContextTrust.TRUSTED,
        priority=100,
        relevance=1.0,
    )


def _request(candidates: tuple[ContextCandidate, ...] = ()) -> ContextAssemblyRequest:
    task_id = new_id("task")
    return ContextAssemblyRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        actor=ActorIdentity("user:issue-650", ActorType.HUMAN),
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-650",
        ),
        candidates=candidates,
        budget=ContextBudget(max_tokens=64_000, max_bytes=256 * 1024, max_items=128),
    )


def _resolve(candidates: tuple[ContextCandidate, ...]):
    return asyncio.run(
        ContextResolver(FakeAuthorizationProvider()).resolve(_request(candidates))
    )


def test_optional_provider_failure_is_auditable_unavailable_omission() -> None:
    request = _request()
    service = OperationalContextAssemblyService(
        ContextAssemblyService(
            ContextResolver(FakeAuthorizationProvider()),
            InMemoryContextBundleRepository(),
        )
    )
    binding = ContextSourceAdapterBinding(
        adapter=_UnavailableAdapter(),
        source_type=ContextSourceType.FILE,
        source_id="file:expected",
        role=ContextEntryRole.CONTEXT,
        mandatory=False,
        record_absence=True,
    )

    bundle = asyncio.run(service.assemble(request, bindings=(binding,)))

    assert bundle.entries == ()
    assert len(bundle.omissions) == 1
    assert bundle.omissions[0].reason is ContextOmissionReason.UNAVAILABLE
    assert bundle.omissions[0].mandatory is False


def test_mandatory_provider_failure_blocks_context_resolution() -> None:
    request = _request()
    service = OperationalContextAssemblyService(
        ContextAssemblyService(
            ContextResolver(FakeAuthorizationProvider()),
            InMemoryContextBundleRepository(),
        )
    )
    binding = ContextSourceAdapterBinding(
        adapter=_UnavailableAdapter(),
        source_type=ContextSourceType.PLAN_STEP,
        source_id="step:required",
        role=ContextEntryRole.CONTEXT,
        mandatory=True,
    )

    with pytest.raises(ContextResolutionError) as exc_info:
        asyncio.run(service.assemble(request, bindings=(binding,)))

    assert exc_info.value.blocker.reason is ContextBlockerReason.MANDATORY_UNAVAILABLE


def test_context_window_requirement_is_a_server_owned_monotonic_constraint() -> None:
    bundle = _resolve((_candidate(content="x" * 16_384),))
    base = RoutingRequirements(min_context_window=8_192, modalities=("text",))
    policy = ContextRoutingPolicy(output_reserve_tokens=2_048)

    merged = merge_context_routing_requirements(base, bundle, policy=policy)
    required = context_window_requirement(bundle, output_reserve_tokens=2_048)

    assert merged.min_context_window == max(8_192, required)
    assert merged.modalities == base.modalities


def test_external_context_egress_fails_closed_for_secret_reference_bundle() -> None:
    secret_value_digest = hashlib.sha256(b"not-stored-secret-value").hexdigest()
    secret = ContextCandidate(
        source=ContextSourceRef(
            source_type=ContextSourceType.SYSTEM_SECURITY,
            source_id="secret:issue-650",
            revision="r1",
            digest=hashlib.sha256(b"secret-reference-r1").hexdigest(),
        ),
        role=ContextEntryRole.CONTEXT,
        selection_reason="authorized secret reference",
        mandatory=True,
        content_ref="secret-ref:issue-650",
        content_digest=secret_value_digest,
        trust=ContextTrust.SYSTEM,
        data_classification=ContextDataClassification.SECRET_REFERENCE,
    )
    bundle = _resolve((secret,))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            ContextBundleEgressExporter().export(
                bundle,
                target=EgressTarget(
                    kind=EgressTargetKind.CONTEXT_EXPORT,
                    target_id="remote-provider",
                    posture=EgressTargetPosture.EXTERNAL,
                ),
                context=OperationContext(
                    correlation_id=bundle.task_id,
                    owner_type="user",
                    owner_id="issue-650",
                ),
            )
        )

    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_control_plane_projection_redacts_source_identity_without_source_read_permission() -> None:
    bundle = _resolve((_candidate(ContextSourceType.REPOSITORY),))
    repository = InMemoryContextBundleRepository()
    repository.put(bundle)
    service = ContextBundleResourceService(repository, visibility=_HiddenVisibility())

    projection = asyncio.run(
        service.get_resource(
            RequestContext(
                request_id="request-650",
                correlation_id="correlation-650",
                actor=ActorContext(
                    principal_ref="user:issue-650",
                    owner_type="user",
                    owner_id="issue-650",
                    actor_type="human",
                ),
            ),
            bundle.context_bundle_id,
        )
    )

    entry = projection["entries"][0]
    assert isinstance(entry, dict)
    assert entry["hidden"] is True
    assert "source_id" not in entry
    assert "source_digest" not in entry
    assert "inline_content" not in entry


def test_restart_reconciliation_repairs_exact_missing_binding_and_is_idempotent() -> None:
    bundle = _resolve((_candidate(),))
    bundles = InMemoryContextBundleRepository()
    bundles.put(bundle)
    agent_run_id = new_id("agent_run")
    record = _PersistedAgentRun(
        agent_run_id=agent_run_id,
        run_id=bundle.run_id,
        task_id=bundle.task_id,
        agent=_AgentRef(bundle.agent_id, bundle.agent_revision),
        telemetry={
            "orchestrator_mapping": {
                "context_bundle_id": bundle.context_bundle_id,
                "context_bundle_digest": bundle.digest,
            }
        },
        orchestrator_adapter_id="reference-context-orchestrator/v1",
        started_at=datetime.now(UTC),
    )
    bindings = InMemoryContextRunBindingRepository()
    reconciler = ContextBindingReconciler(
        agents=_AgentRunRepository((record,)),  # type: ignore[arg-type]
        bundles=bundles,
        bindings=bindings,
    )

    first = reconciler.reconcile()
    second = reconciler.reconcile()

    assert first.repaired == 1
    assert first.already_bound == 0
    assert second.repaired == 0
    assert second.already_bound == 1
    assert bindings.get(agent_run_id).context_bundle_digest == bundle.digest
