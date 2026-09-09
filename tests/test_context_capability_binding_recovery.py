from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from ai_multi_agent_platform.agents import AgentRepository, AgentRunStatus
from ai_multi_agent_platform.capabilities import CapabilityInvocation, InvocationTrace
from ai_multi_agent_platform.context import (
    ContextBudget,
    ContextBudgetUsage,
    ContextBundle,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextRunBinding,
    ContextSourceRef,
    ContextSourceType,
    InMemoryContextBundleRepository,
    InMemoryContextRunBindingRepository,
)
from ai_multi_agent_platform.contracts import DataClassification, OperationContext
from ai_multi_agent_platform.deployment.context_operationalization import (
    _bound_capability_classification,
)
from ai_multi_agent_platform.domain import new_id


def _bundle(
    *,
    task_id: str,
    run_id: str,
    agent_id: str,
    content: str,
    classification: ContextDataClassification,
) -> ContextBundle:
    encoded = content.encode("utf-8")
    entry = ContextEntry(
        ordinal=0,
        source=ContextSourceRef(ContextSourceType.TASK, task_id),
        role=ContextEntryRole.CONTEXT,
        selection_reason="capability recovery regression",
        mandatory=True,
        content_digest=hashlib.sha256(encoded).hexdigest(),
        inline_content=content,
        data_classification=classification,
        estimated_tokens=4,
        content_bytes=len(encoded),
    )
    return ContextBundle(
        context_bundle_id=new_id("context_bundle"),
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        agent_revision=1,
        entries=(entry,),
        omissions=(),
        budget=ContextBudget(max_tokens=64),
        usage=ContextBudgetUsage(
            estimated_tokens=entry.estimated_tokens,
            bytes=entry.content_bytes,
            items=1,
        ),
        resolver_version="capability-recovery-test/v1",
        policy_version="capability-recovery-test/v1",
        actor_ref="user:capability-recovery-test",
    )


class _AgentRuns:
    def __init__(self, records: tuple[SimpleNamespace, ...]) -> None:
        self.records = records

    def list_agent_runs(self, run_id: str | None = None) -> tuple[SimpleNamespace, ...]:
        if run_id is None:
            return self.records
        return tuple(record for record in self.records if record.run_id == run_id)


def test_capability_classification_uses_current_agent_run_binding_after_retry() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    failed_agent_run_id = new_id("agent_run")
    current_agent_run_id = new_id("agent_run")

    historical = _bundle(
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        content="historical internal context",
        classification=ContextDataClassification.INTERNAL,
    )
    current = _bundle(
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        content="retry restricted context",
        classification=ContextDataClassification.RESTRICTED,
    )
    bundles = InMemoryContextBundleRepository()
    bundles.put(historical)
    bundles.put(current)

    bindings = InMemoryContextRunBindingRepository()
    created_at = datetime.now(UTC)
    bindings.put(
        ContextRunBinding(
            agent_run_id=failed_agent_run_id,
            run_id=run_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_revision=1,
            context_bundle_id=historical.context_bundle_id,
            context_bundle_digest=historical.digest,
            resolver_version=historical.resolver_version,
            policy_version=historical.policy_version,
            orchestrator_adapter_id="retry-regression",
            created_at=created_at,
        )
    )
    bindings.put(
        ContextRunBinding(
            agent_run_id=current_agent_run_id,
            run_id=run_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_revision=1,
            context_bundle_id=current.context_bundle_id,
            context_bundle_digest=current.digest,
            resolver_version=current.resolver_version,
            policy_version=current.policy_version,
            orchestrator_adapter_id="retry-regression",
            created_at=created_at + timedelta(seconds=1),
        )
    )

    agent_ref = SimpleNamespace(agent_id=agent_id, revision=1)
    agents = _AgentRuns(
        (
            SimpleNamespace(
                agent_run_id=failed_agent_run_id,
                run_id=run_id,
                task_id=task_id,
                agent=agent_ref,
                status=AgentRunStatus.FAILED,
            ),
            SimpleNamespace(
                agent_run_id=current_agent_run_id,
                run_id=run_id,
                task_id=task_id,
                agent=agent_ref,
                status=AgentRunStatus.RUNNING,
            ),
        )
    )
    operation = OperationContext(correlation_id="capability-binding-recovery")
    request = CapabilityInvocation(
        invocation_id="capability-binding-recovery",
        capability_id="capability_recovery_test",
        arguments={},
        context=operation,
        trace=InvocationTrace(
            correlation_id=operation.correlation_id,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
        ),
    )

    classification = _bound_capability_classification(
        request,
        agents=cast(AgentRepository, agents),
        bundles=bundles,
        run_bindings=bindings,
    )

    assert len(bundles.list_for_run(run_id)) == 2
    assert classification is DataClassification.RESTRICTED
