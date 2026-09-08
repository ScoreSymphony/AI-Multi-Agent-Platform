"""Canonical #592 -> #590 bridge for consumed Agent Handoffs.

The handoff domain remains authoritative for work-transfer identity and consumption.
This module only projects an already-durable consuming-run binding into the context
assembly boundary; it does not create a second Handoff lifecycle or dereference source
Artifacts/Results/Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents.models import AgentRevisionRef
from ai_multi_agent_platform.context import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceRequest,
    ContextSourceType,
    ContextTrust,
)

from .models import HandoffRuntimeContext


def _validate_runtime_context(runtime_context: HandoffRuntimeContext) -> None:
    handoff = runtime_context.handoff
    consumption = runtime_context.consumption
    source = runtime_context.context_source

    if (
        consumption.handoff_id != handoff.handoff_id
        or consumption.handoff_revision != handoff.revision
        or consumption.handoff_digest != handoff.content_digest
    ):
        raise ValueError("handoff runtime consumption does not match exact handoff identity")
    if (
        source.handoff_id != handoff.handoff_id
        or source.revision != handoff.revision
        or source.digest != handoff.content_digest
    ):
        raise ValueError("handoff context source does not match exact handoff identity")
    if (
        source.task_id != handoff.content.task_id
        or source.plan_id != handoff.content.plan_id
        or source.producer_step_id != handoff.content.producer_step_id
        or source.consumer_step_id != handoff.content.consumer_step_id
    ):
        raise ValueError("handoff context source execution references do not match handoff content")


def handoff_context_candidate(
    runtime_context: HandoffRuntimeContext,
    *,
    mandatory: bool = True,
) -> ContextCandidate:
    """Project one durably consumed Handoff into a canonical #590 context candidate.

    Producer statements are deliberately ``UNTRUSTED`` context. Referenced source
    resources retain their own authorization/verification authority and are not copied
    or promoted by this bridge.
    """

    _validate_runtime_context(runtime_context)
    source = runtime_context.context_source
    content_ref = f"handoff:{source.handoff_id}@{source.revision}"
    return ContextCandidate(
        source=ContextSourceRef(
            source_type=ContextSourceType.AGENT_HANDOFF,
            source_id=source.handoff_id,
            revision=str(source.revision),
            digest=source.digest,
            locator=content_ref,
        ),
        role=ContextEntryRole.CONTEXT,
        selection_reason=source.selection_reason,
        mandatory=mandatory,
        content_ref=content_ref,
        content_digest=source.digest,
        trust=ContextTrust.UNTRUSTED,
        data_classification=ContextDataClassification.INTERNAL,
        relevance=1.0,
        metadata={
            "task_id": source.task_id,
            "plan_id": source.plan_id,
            "producer_step_id": source.producer_step_id,
            "consumer_step_id": source.consumer_step_id,
            "consuming_run_id": runtime_context.consumption.consuming_run_id,
        },
    )


@dataclass(frozen=True, slots=True)
class ConsumedHandoffContextAdapter:
    """#590 source adapter over exact Handoffs already bound to consuming Runs."""

    runtime_contexts: tuple[HandoffRuntimeContext, ...]
    adapter_id: str = "canonical-agent-handoff"

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ValueError("handoff context adapter ID must not be blank")
        contexts = tuple(self.runtime_contexts)
        identities: set[tuple[str, int, str]] = set()
        for runtime_context in contexts:
            _validate_runtime_context(runtime_context)
            identity = (
                runtime_context.handoff.handoff_id,
                runtime_context.handoff.revision,
                runtime_context.consumption.consuming_run_id,
            )
            if identity in identities:
                raise ValueError("duplicate consumed handoff runtime context")
            identities.add(identity)
        object.__setattr__(self, "runtime_contexts", contexts)

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        candidates: list[ContextCandidate] = []
        for runtime_context in self.runtime_contexts:
            handoff = runtime_context.handoff
            consumption = runtime_context.consumption
            if handoff.task_id != request.task_id or consumption.consuming_run_id != request.run_id:
                continue
            if request.plan_id is not None and handoff.content.plan_id != request.plan_id:
                continue
            if request.step_id is not None and handoff.content.consumer_step_id != request.step_id:
                continue
            consumer = consumption.consumer
            if isinstance(consumer, AgentRevisionRef) and (
                consumer.agent_id != request.agent_id or consumer.revision != request.agent_revision
            ):
                continue
            candidates.append(handoff_context_candidate(runtime_context))
        return tuple(sorted(candidates, key=lambda candidate: candidate.source.canonical_key))
