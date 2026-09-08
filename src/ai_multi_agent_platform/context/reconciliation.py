"""Restart reconciliation for Context Bundle -> AgentRun binding evidence."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentRepository

from .bindings import (
    ContextRunBindingRepository,
    context_binding_from_agent_run_metadata,
)
from .resolver import ContextBundleRepository


@dataclass(frozen=True, slots=True)
class ContextBindingReconciliationReport:
    scanned_agent_runs: int
    already_bound: int
    repaired: int
    non_context_runs: int


class ContextBindingReconciler:
    """Repair only bindings provable from persisted AgentRun + exact Bundle evidence."""

    def __init__(
        self,
        *,
        agents: AgentRepository,
        bundles: ContextBundleRepository,
        bindings: ContextRunBindingRepository,
    ) -> None:
        self.agents = agents
        self.bundles = bundles
        self.bindings = bindings

    def reconcile(self) -> ContextBindingReconciliationReport:
        already_bound = 0
        repaired = 0
        non_context_runs = 0
        records = self.agents.list_agent_runs()
        for record in records:
            mapping = record.telemetry.get("orchestrator_mapping")
            if not isinstance(mapping, dict):
                non_context_runs += 1
                continue
            bundle_id = mapping.get("context_bundle_id")
            bundle_digest = mapping.get("context_bundle_digest")
            if bundle_id is None and bundle_digest is None:
                non_context_runs += 1
                continue
            if not isinstance(bundle_id, str) or not bundle_id.strip():
                raise ValueError(
                    f"AgentRun {record.agent_run_id} has incomplete Context Bundle ID evidence"
                )
            if not isinstance(bundle_digest, str) or not bundle_digest.strip():
                raise ValueError(
                    f"AgentRun {record.agent_run_id} has incomplete Context Bundle digest evidence"
                )
            bundle = self.bundles.get(bundle_id)
            if bundle.digest != bundle_digest:
                raise ValueError(
                    f"AgentRun {record.agent_run_id} Context Bundle digest does not match storage"
                )
            try:
                existing = self.bindings.get(record.agent_run_id)
            except KeyError:
                existing = None
            if existing is not None:
                if (
                    existing.context_bundle_id != bundle.context_bundle_id
                    or existing.context_bundle_digest != bundle.digest
                ):
                    raise ValueError(
                        f"AgentRun {record.agent_run_id} has conflicting Context binding evidence"
                    )
                already_bound += 1
                continue
            recovered = context_binding_from_agent_run_metadata(record, bundle)
            self.bindings.put(recovered)
            repaired += 1
        return ContextBindingReconciliationReport(
            scanned_agent_runs=len(records),
            already_bound=already_bound,
            repaired=repaired,
            non_context_runs=non_context_runs,
        )


def reconcile_context_run_bindings(
    *,
    agents: AgentRepository,
    bundles: ContextBundleRepository,
    bindings: ContextRunBindingRepository,
) -> ContextBindingReconciliationReport:
    return ContextBindingReconciler(
        agents=agents,
        bundles=bundles,
        bindings=bindings,
    ).reconcile()
