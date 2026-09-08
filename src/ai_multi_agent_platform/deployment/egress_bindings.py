"""Composition helpers for wiring one #591 egress runtime across provider boundaries.

This module intentionally lives in the deployment layer. Domain registries keep ownership of
provider/model/capability/connector identity; these helpers only make it difficult for production
composition to accidentally construct separate policy gates for different outbound paths.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import (
    ApprovalHook,
    CanonicalInvocationBindingHook,
    CapabilityClassificationResolver,
    CapabilityRegistry,
    EgressCapabilityInvoker,
    GovernanceBindingHook,
    InvocationObserver,
    PolicyHook,
)
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorRepository,
    EgressConnectorService,
)
from ai_multi_agent_platform.connectors.egress import ConnectorClassificationResolver
from ai_multi_agent_platform.context import ContextBundleEgressExporter
from ai_multi_agent_platform.contracts import FileProvider
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.data import FileEgressExporter
from ai_multi_agent_platform.models import DeterministicModelRouter, ModelRegistry, ModelRuntime
from ai_multi_agent_platform.security import AuthorizationGate
from ai_multi_agent_platform.security.classification_control_plane import (
    register_data_classification_control_plane,
)
from ai_multi_agent_platform.security.egress_composition import DurableEgressRuntime
from ai_multi_agent_platform.security.egress_control_plane import (
    register_egress_profile_control_plane,
)
from ai_multi_agent_platform.security.egress_policy_control_plane import (
    register_egress_policy_inspection,
)


@dataclass(frozen=True, slots=True)
class EgressDeploymentBindings:
    """Factories that force all outbound adapters to share one durable EgressGate."""

    runtime: DurableEgressRuntime

    def model_runtime(
        self,
        registry: ModelRegistry,
        router: DeterministicModelRouter | None = None,
    ) -> ModelRuntime:
        return ModelRuntime(registry, router, egress_gate=self.runtime.gate)

    def capability_invoker(
        self,
        registry: CapabilityRegistry,
        *,
        policy_hook: PolicyHook | None = None,
        canonical_binding_hook: CanonicalInvocationBindingHook | None = None,
        governance_binding_hook: GovernanceBindingHook | None = None,
        approval_hook: ApprovalHook | None = None,
        observer: InvocationObserver | None = None,
        classification_resolver: CapabilityClassificationResolver | None = None,
    ) -> EgressCapabilityInvoker:
        return EgressCapabilityInvoker(
            registry,
            policy_hook=policy_hook,
            canonical_binding_hook=canonical_binding_hook,
            governance_binding_hook=governance_binding_hook,
            approval_hook=approval_hook,
            observer=observer,
            egress_gate=self.runtime.gate,
            classification_resolver=classification_resolver,
        )

    def connector_service(
        self,
        repository: ConnectorRepository,
        registry: ConnectorRegistry,
        *,
        authorization_gate: AuthorizationGate | None = None,
        classification_resolver: ConnectorClassificationResolver | None = None,
    ) -> EgressConnectorService:
        return EgressConnectorService(
            repository,
            registry,
            authorization_gate=authorization_gate,
            egress_gate=self.runtime.gate,
            classification_resolver=classification_resolver,
        )

    def context_exporter(self) -> ContextBundleEgressExporter:
        return ContextBundleEgressExporter(egress_gate=self.runtime.gate)

    def file_exporter(self, provider: FileProvider) -> FileEgressExporter:
        return FileEgressExporter(provider, egress_gate=self.runtime.gate)

    def register_control_plane(self, control_plane: ControlPlane) -> None:
        """Register vocabulary, policy lifecycle and side-effect-free compatibility inspection."""

        register_data_classification_control_plane(control_plane)
        register_egress_profile_control_plane(control_plane, self.runtime.profiles)
        register_egress_policy_inspection(
            control_plane,
            self.runtime.repository,
            self.runtime.gate,
        )


__all__ = ["EgressDeploymentBindings"]
