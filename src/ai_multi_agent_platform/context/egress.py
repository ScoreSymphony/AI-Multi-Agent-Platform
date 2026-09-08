"""Policy-controlled export boundary for immutable Context Bundles."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    ErrorCode,
    OperationContext,
    strongest_classification,
)
from ai_multi_agent_platform.security.egress import EgressGate

from .models import ContextBundle
from .rendering import ContextContentProvider, ContextRenderer, ReferenceContextRenderer, RenderedContext


class ContextBundleEgressExporter:
    """The canonical outbound Context Bundle path; rendering happens only after policy allows."""

    def __init__(
        self,
        *,
        egress_gate: EgressGate | None = None,
        renderer: ContextRenderer | None = None,
    ) -> None:
        self.egress_gate = egress_gate or EgressGate()
        self.renderer = renderer or ReferenceContextRenderer()

    async def export(
        self,
        bundle: ContextBundle,
        *,
        target: EgressTarget,
        context: OperationContext,
        content_provider: ContextContentProvider | None = None,
    ) -> RenderedContext:
        if target.kind is not EgressTargetKind.CONTEXT_EXPORT:
            raise ValueError("context bundle export requires target kind context_export")
        classification = _bundle_classification(bundle)
        decision = await self.egress_gate.enforce(
            EgressRequest(
                request_id=f"context:{bundle.context_bundle_id}:{target.target_id}",
                target=target,
                context=context,
                classification=classification,
                resource_type="context_bundle",
                payload_digest=bundle.digest,
                task_id=bundle.task_id,
                run_id=bundle.run_id,
                policy_descriptors={
                    "context_bundle_id": bundle.context_bundle_id,
                    "entry_count": len(bundle.entries),
                },
            )
        )
        if decision.effective_classification is not classification:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "context egress downgrade requires creation of an explicit redacted Context Bundle",
                details={"egress_request_id": decision.request_id},
            )
        return await self.renderer.render(
            bundle,
            content_provider=content_provider,
            allow_secret_resolution=classification
            not in {DataClassification.SECRET, DataClassification.SECRET_REFERENCE},
        )


def _bundle_classification(bundle: ContextBundle) -> DataClassification:
    classifications = tuple(DataClassification(entry.data_classification.value) for entry in bundle.entries)
    return strongest_classification(*classifications) or DataClassification.PUBLIC
