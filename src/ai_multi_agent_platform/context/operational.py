"""Production-shaped execution helpers for canonical Context Bundles.

This module operationalizes the existing #590 boundary without changing Context Bundle
ownership. It composes context-derived model routing, egress enforcement, context-aware
AgentRun binding, and the exact rendered model input used by execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from ai_multi_agent_platform.agents import (
    AgentExecutionSpec,
    AgentOrchestratorMapper,
    AgentRunRecord,
    AgentRuntime,
    OrchestratorMapping,
)
from ai_multi_agent_platform.contracts import (
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import ModelLocation, ModelRegistry, RoutingRequirements
from ai_multi_agent_platform.security.egress import EgressGate

from .bindings import (
    ContextAwareOrchestratorAdapter,
    ContextRunBinding,
    ContextRunBindingRepository,
    InMemoryContextRunBindingRepository,
    ReferenceContextOrchestratorAdapter,
)
from .egress import ContextBundleEgressExporter
from .models import ContextBundle, ContextEntryRole
from .persistence import InMemoryContextBundleRepository
from .rendering import (
    ContextContentProvider,
    ContextRenderer,
    ReferenceContextRenderer,
    RenderedContext,
    assert_render_preserves_bundle,
)
from .resolver import ContextBundleRepository, context_window_requirement


@dataclass(frozen=True, slots=True)
class ContextRoutingPolicy:
    """Explicit provider-neutral reserve applied before #10 model selection."""

    output_reserve_tokens: int = 0

    def __post_init__(self) -> None:
        if self.output_reserve_tokens < 0:
            raise ValueError("context output reserve tokens must be >= 0")


@dataclass(frozen=True, slots=True)
class ContextModelInput:
    """Role-preserving model input derived from one already-rendered Context Bundle."""

    system_instruction: str
    user_message: str

    def __post_init__(self) -> None:
        if not self.system_instruction.strip():
            raise ValueError("operational context requires instruction/security content")
        if not self.user_message.strip():
            raise ValueError("operational context requires task/context/evidence content")


@dataclass(frozen=True, slots=True)
class OperationalContextExecution:
    """Exact evidence and model input produced for one context-bound AgentRun."""

    agent_run: AgentRunRecord
    binding: ContextRunBinding
    rendered: RenderedContext
    model_input: ContextModelInput


class ContextEgressTargetResolver(Protocol):
    """Resolve the trust boundary for the model selected by canonical #10 routing."""

    def resolve(
        self,
        spec: AgentExecutionSpec,
        bundle: ContextBundle,
    ) -> EgressTarget | None: ...


class ModelRegistryContextEgressTargetResolver:
    """Resolve canonical model location into a Context egress trust boundary.

    Local models remain in-process. Self-hosted and remote models cross the canonical egress
    boundary before rendering. Operators may override a provider target with an explicit
    classification allowlist through ``target_factory`` without leaking provider-native model
    identifiers into Context Bundle state.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        target_factory: Mapping[str, EgressTarget] | None = None,
    ) -> None:
        self.registry = registry
        self.target_factory = dict(target_factory or {})

    def resolve(
        self,
        spec: AgentExecutionSpec,
        bundle: ContextBundle,
    ) -> EgressTarget | None:
        del bundle
        model_id = spec.selected_model_config_id
        if model_id is None:
            return None
        model = self.registry.get_model(model_id)
        if model.location is ModelLocation.LOCAL:
            return None
        configured = self.target_factory.get(model.provider_id)
        if configured is not None:
            if configured.kind is not EgressTargetKind.CONTEXT_EXPORT:
                raise ValueError("configured model Context egress target must use context_export")
            return configured

        posture = (
            EgressTargetPosture.INTERNAL
            if model.location is ModelLocation.SELF_HOSTED
            else EgressTargetPosture.EXTERNAL
        )
        return EgressTarget(
            kind=EgressTargetKind.CONTEXT_EXPORT,
            target_id=model.provider_id,
            posture=posture,
        )


def merge_context_routing_requirements(
    base: RoutingRequirements | None,
    bundle: ContextBundle,
    *,
    policy: ContextRoutingPolicy = ContextRoutingPolicy(),
) -> RoutingRequirements:
    """Monotonically add the effective Bundle size to an existing routing requirement set."""

    current = base or RoutingRequirements()
    required = context_window_requirement(
        bundle,
        output_reserve_tokens=policy.output_reserve_tokens,
    )
    if required <= 0:
        return current
    effective = max(current.min_context_window or 0, required)
    return replace(current, min_context_window=effective)


def rendered_context_model_input(rendered: RenderedContext) -> ContextModelInput:
    """Preserve #590 role authority while converting provider-neutral parts to model text."""

    instruction_parts: list[str] = []
    context_parts: list[str] = []
    for part in rendered.parts:
        labelled = (
            f"[context:{part.ordinal}:{part.source_type.value}:{part.source_id}]\n{part.content}"
        )
        if part.role in {ContextEntryRole.SECURITY, ContextEntryRole.INSTRUCTION}:
            instruction_parts.append(labelled)
        else:
            context_parts.append(labelled)
    return ContextModelInput(
        system_instruction="\n\n".join(instruction_parts),
        user_message="\n\n".join(context_parts),
    )


class _OperationalContextMapper(AgentOrchestratorMapper):
    def __init__(
        self,
        *,
        bundle: ContextBundle,
        adapter: ContextAwareOrchestratorAdapter,
        renderer: ContextRenderer,
        exporter: ContextBundleEgressExporter,
        operation: OperationContext,
        target_resolver: ContextEgressTargetResolver | None,
        content_provider: ContextContentProvider | None,
    ) -> None:
        self.bundle = bundle
        self.adapter = adapter
        self.renderer = renderer
        self.exporter = exporter
        self.operation = operation
        self.target_resolver = target_resolver
        self.content_provider = content_provider
        self.rendered: RenderedContext | None = None

    @property
    def adapter_id(self) -> str:
        return self.adapter.adapter_id

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        self._validate_spec(spec)
        target = (
            None
            if self.target_resolver is None
            else self.target_resolver.resolve(spec, self.bundle)
        )
        if target is None:
            rendered = await self.renderer.render(
                self.bundle,
                content_provider=self.content_provider,
            )
        else:
            rendered = await self.exporter.export(
                self.bundle,
                target=target,
                context=self.operation,
                content_provider=self.content_provider,
            )
        assert_render_preserves_bundle(self.bundle, rendered)
        self.rendered = rendered

        mapping = await self.adapter.map_agent_with_context(spec, self.bundle, rendered)
        if mapping.adapter_id != self.adapter_id:
            raise ValueError("context-aware adapter returned a different adapter ID")
        metadata = dict(mapping.metadata)
        reserved: dict[str, JsonValue] = {
            "context_bundle_id": self.bundle.context_bundle_id,
            "context_bundle_digest": self.bundle.digest,
            "context_renderer_id": rendered.renderer_id,
        }
        if target is not None:
            reserved["context_egress_target_id"] = target.target_id
            reserved["context_egress_target_posture"] = target.posture.value
        for key, value in reserved.items():
            existing = metadata.get(key)
            if existing is not None and existing != value:
                raise ValueError(f"context-aware adapter changed reserved metadata {key!r}")
            metadata[key] = value
        return OrchestratorMapping(
            adapter_id=mapping.adapter_id,
            runtime_ref=mapping.runtime_ref,
            metadata=metadata,
        )

    def _validate_spec(self, spec: AgentExecutionSpec) -> None:
        if spec.task_id != self.bundle.task_id or spec.run_id != self.bundle.run_id:
            raise ValueError("Context Bundle Task/Run does not match Agent execution")
        revision = spec.agent_revision
        if (
            revision.agent_id != self.bundle.agent_id
            or revision.revision != self.bundle.agent_revision
        ):
            raise ValueError("Context Bundle Agent revision does not match Agent execution")
        if spec.task_context or spec.project_context:
            raise ValueError(
                "context-bound execution cannot mix canonical Context Bundle "
                "with legacy adapter-private task/project context"
            )


class OperationalContextBoundAgentRuntime:
    """Production-shaped composition of #590 context with #33/#10 execution."""

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        bundle_repository: ContextBundleRepository | None = None,
        binding_repository: ContextRunBindingRepository | None = None,
        renderer: ContextRenderer | None = None,
        egress_gate: EgressGate | None = None,
        target_resolver: ContextEgressTargetResolver | None = None,
        routing_policy: ContextRoutingPolicy = ContextRoutingPolicy(),
    ) -> None:
        self.runtime = runtime
        self.bundle_repository = bundle_repository or InMemoryContextBundleRepository()
        self.binding_repository = binding_repository or InMemoryContextRunBindingRepository()
        self.renderer = renderer or ReferenceContextRenderer()
        self.exporter = ContextBundleEgressExporter(
            egress_gate=egress_gate,
            renderer=self.renderer,
        )
        self.target_resolver = target_resolver
        self.routing_policy = routing_policy

    async def start_agent(
        self,
        *,
        bundle: ContextBundle,
        operation: OperationContext,
        adapter: ContextAwareOrchestratorAdapter | None = None,
        content_provider: ContextContentProvider | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        verification_context: Mapping[str, JsonValue] | None = None,
    ) -> OperationalContextExecution:
        stored_bundle = self.bundle_repository.put(bundle)
        runtime_requirements = merge_context_routing_requirements(
            None,
            stored_bundle,
            policy=self.routing_policy,
        )
        selected_adapter = adapter or ReferenceContextOrchestratorAdapter()
        mapper = _OperationalContextMapper(
            bundle=stored_bundle,
            adapter=selected_adapter,
            renderer=self.renderer,
            exporter=self.exporter,
            operation=operation,
            target_resolver=self.target_resolver,
            content_provider=content_provider,
        )
        record = await self.runtime.start_agent(
            task_id=stored_bundle.task_id,
            run_id=stored_bundle.run_id,
            agent_id=stored_bundle.agent_id,
            revision=stored_bundle.agent_revision,
            mapper=mapper,
            task_model_override=task_model_override,
            runtime_model_requirements=runtime_requirements,
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=None,
            project_context=None,
            verification_context=verification_context,
        )
        rendered = mapper.rendered
        if rendered is None:
            raise RuntimeError("context-aware Agent mapping did not produce rendered context")
        binding = ContextRunBinding(
            agent_run_id=record.agent_run_id,
            run_id=record.run_id,
            task_id=record.task_id,
            agent_id=record.agent.agent_id,
            agent_revision=record.agent.revision,
            context_bundle_id=stored_bundle.context_bundle_id,
            context_bundle_digest=stored_bundle.digest,
            resolver_version=stored_bundle.resolver_version,
            policy_version=stored_bundle.policy_version,
            orchestrator_adapter_id=record.orchestrator_adapter_id or selected_adapter.adapter_id,
            created_at=datetime.now(UTC),
        )
        stored_binding = self.binding_repository.put(binding)
        return OperationalContextExecution(
            agent_run=record,
            binding=stored_binding,
            rendered=rendered,
            model_input=rendered_context_model_input(rendered),
        )
