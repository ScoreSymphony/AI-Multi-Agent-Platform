"""AgentRun binding and context-aware orchestrator seam for issue #590."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ai_multi_agent_platform.agents import (
    AgentExecutionSpec,
    AgentOrchestratorMapper,
    AgentRunRecord,
    AgentRuntime,
    AgentTeamRevision,
    OrchestratorMapping,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.models import RoutingRequirements

from .models import ContextBundle
from .persistence import InMemoryContextBundleRepository
from .rendering import (
    ContextContentProvider,
    ContextRenderer,
    ReferenceContextRenderer,
    RenderedContext,
    assert_render_preserves_bundle,
)


@dataclass(frozen=True, slots=True)
class ContextRunBinding:
    """Immutable historical proof of the effective Context Bundle used by one AgentRun."""

    agent_run_id: str
    run_id: str
    task_id: str
    agent_id: str
    agent_revision: int
    context_bundle_id: str
    context_bundle_digest: str
    resolver_version: str
    policy_version: str
    orchestrator_adapter_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        validate_id(self.agent_run_id, "agent_run")
        validate_id(self.run_id, "run")
        validate_id(self.task_id, "task")
        validate_id(self.agent_id, "agent")
        validate_id(self.context_bundle_id, "context_bundle")
        if self.agent_revision < 1:
            raise ValueError("context run binding agent revision must be >= 1")
        for value, name in (
            (self.context_bundle_digest, "context bundle digest"),
            (self.resolver_version, "resolver version"),
            (self.policy_version, "policy version"),
            (self.orchestrator_adapter_id, "orchestrator adapter ID"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "agent_run_id": self.agent_run_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "agent_revision": self.agent_revision,
            "context_bundle_id": self.context_bundle_id,
            "context_bundle_digest": self.context_bundle_digest,
            "resolver_version": self.resolver_version,
            "policy_version": self.policy_version,
            "orchestrator_adapter_id": self.orchestrator_adapter_id,
            "created_at": self.created_at.isoformat(),
        }


class ContextRunBindingRepository(Protocol):
    def put(self, binding: ContextRunBinding) -> ContextRunBinding: ...

    def get(self, agent_run_id: str) -> ContextRunBinding: ...

    def list_for_run(self, run_id: str) -> tuple[ContextRunBinding, ...]: ...

    def list_all(self) -> tuple[ContextRunBinding, ...]: ...


class InMemoryContextRunBindingRepository:
    def __init__(self) -> None:
        self._bindings: dict[str, ContextRunBinding] = {}

    def put(self, binding: ContextRunBinding) -> ContextRunBinding:
        current = self._bindings.get(binding.agent_run_id)
        if current is not None:
            if current != binding:
                raise ValueError("AgentRun Context Bundle binding is immutable")
            return current
        self._bindings[binding.agent_run_id] = binding
        return binding

    def get(self, agent_run_id: str) -> ContextRunBinding:
        try:
            return self._bindings[agent_run_id]
        except KeyError as exc:
            raise KeyError(f"context run binding not found: {agent_run_id}") from exc

    def list_for_run(self, run_id: str) -> tuple[ContextRunBinding, ...]:
        return tuple(
            sorted(
                (item for item in self._bindings.values() if item.run_id == run_id),
                key=lambda item: (item.created_at, item.agent_run_id),
            )
        )

    def list_all(self) -> tuple[ContextRunBinding, ...]:
        return tuple(
            sorted(
                self._bindings.values(),
                key=lambda item: (item.created_at, item.agent_run_id),
            )
        )


class JsonContextRunBindingRepository(InMemoryContextRunBindingRepository):
    """Durable local binding evidence, independent from provider/orchestrator state."""

    SCHEMA = "context-run-binding-repository/v1"

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def put(self, binding: ContextRunBinding) -> ContextRunBinding:
        current = self._bindings.get(binding.agent_run_id)
        if current is not None:
            return super().put(binding)
        stored = super().put(binding)
        self._save()
        return stored

    def _save(self) -> None:
        document = {
            "schema": self.SCHEMA,
            "bindings": [item.to_json() for item in self.list_all()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _restore(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != self.SCHEMA:
            raise ValueError("unsupported context run binding repository schema")
        bindings = raw.get("bindings")
        if not isinstance(bindings, list):
            raise ValueError("context run binding repository bindings must be an array")
        for value in bindings:
            if not isinstance(value, dict):
                raise ValueError("context run binding must be a JSON object")
            super().put(_binding_from_json(value))


class ContextAwareOrchestratorAdapter(Protocol):
    """Adapter receives the already-resolved immutable Context Bundle rendering."""

    @property
    def adapter_id(self) -> str: ...

    async def map_agent_with_context(
        self,
        spec: AgentExecutionSpec,
        bundle: ContextBundle,
        rendered: RenderedContext,
    ) -> OrchestratorMapping: ...


class ReferenceContextOrchestratorAdapter:
    """Hermes-free proof that an AgentRun consumes canonical context, not private prompts."""

    adapter_id = "reference-context-orchestrator"

    async def map_agent_with_context(
        self,
        spec: AgentExecutionSpec,
        bundle: ContextBundle,
        rendered: RenderedContext,
    ) -> OrchestratorMapping:
        assert_render_preserves_bundle(bundle, rendered)
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=(
                f"context-reference:{spec.agent_revision.agent_id}:"
                f"r{spec.agent_revision.revision}:{spec.run_id}"
            ),
            metadata={
                "context_bundle_id": bundle.context_bundle_id,
                "context_bundle_digest": bundle.digest,
                "context_entry_digests": [part.content_digest for part in rendered.parts],
                "context_renderer_id": rendered.renderer_id,
            },
        )


class _BoundContextMapper(AgentOrchestratorMapper):
    def __init__(
        self,
        *,
        bundle: ContextBundle,
        adapter: ContextAwareOrchestratorAdapter,
        renderer: ContextRenderer,
        content_provider: ContextContentProvider | None,
    ) -> None:
        self.bundle = bundle
        self.adapter = adapter
        self.renderer = renderer
        self.content_provider = content_provider

    @property
    def adapter_id(self) -> str:
        return self.adapter.adapter_id

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        self._validate_spec(spec)
        rendered = await self.renderer.render(
            self.bundle,
            content_provider=self.content_provider,
        )
        assert_render_preserves_bundle(self.bundle, rendered)
        mapping = await self.adapter.map_agent_with_context(spec, self.bundle, rendered)
        if mapping.adapter_id != self.adapter_id:
            raise ValueError("context-aware adapter returned a different adapter ID")
        metadata = dict(mapping.metadata)
        for key, value in {
            "context_bundle_id": self.bundle.context_bundle_id,
            "context_bundle_digest": self.bundle.digest,
            "context_renderer_id": rendered.renderer_id,
        }.items():
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


class ContextBoundAgentRuntime:
    """Compose #33 AgentRuntime with the canonical #590 context boundary."""

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        bundle_repository: InMemoryContextBundleRepository | None = None,
        binding_repository: ContextRunBindingRepository | None = None,
        renderer: ContextRenderer | None = None,
    ) -> None:
        self.runtime = runtime
        self.bundle_repository = bundle_repository or InMemoryContextBundleRepository()
        self.binding_repository = binding_repository or InMemoryContextRunBindingRepository()
        self.renderer = renderer or ReferenceContextRenderer()

    async def start_agent(
        self,
        *,
        bundle: ContextBundle,
        adapter: ContextAwareOrchestratorAdapter | None = None,
        content_provider: ContextContentProvider | None = None,
        team_revision: AgentTeamRevision | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        shared_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        verification_context: Mapping[str, JsonValue] | None = None,
    ) -> tuple[AgentRunRecord, ContextRunBinding]:
        stored_bundle = self.bundle_repository.put(bundle)
        selected_adapter = adapter or ReferenceContextOrchestratorAdapter()
        mapper = _BoundContextMapper(
            bundle=stored_bundle,
            adapter=selected_adapter,
            renderer=self.renderer,
            content_provider=content_provider,
        )
        record = await self.runtime.start_agent(
            task_id=stored_bundle.task_id,
            run_id=stored_bundle.run_id,
            agent_id=stored_bundle.agent_id,
            revision=stored_bundle.agent_revision,
            mapper=mapper,
            team_revision=team_revision,
            task_model_override=task_model_override,
            requested_capability_ids=requested_capability_ids,
            shared_capability_ids=shared_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=None,
            project_context=None,
            verification_context=verification_context,
        )
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
        return record, self.binding_repository.put(binding)


def context_binding_from_agent_run_metadata(
    record: AgentRunRecord,
    bundle: ContextBundle,
) -> ContextRunBinding:
    """Recover binding evidence after a crash between AgentRun persistence and binding write."""

    mapping = record.telemetry.get("orchestrator_mapping")
    if not isinstance(mapping, dict):
        raise ValueError("AgentRun has no canonical orchestrator mapping evidence")
    if mapping.get("context_bundle_id") != bundle.context_bundle_id:
        raise ValueError("AgentRun mapping does not reference the supplied Context Bundle")
    if mapping.get("context_bundle_digest") != bundle.digest:
        raise ValueError("AgentRun mapping Context Bundle digest does not match")
    return ContextRunBinding(
        agent_run_id=record.agent_run_id,
        run_id=record.run_id,
        task_id=record.task_id,
        agent_id=record.agent.agent_id,
        agent_revision=record.agent.revision,
        context_bundle_id=bundle.context_bundle_id,
        context_bundle_digest=bundle.digest,
        resolver_version=bundle.resolver_version,
        policy_version=bundle.policy_version,
        orchestrator_adapter_id=record.orchestrator_adapter_id or "unknown",
        created_at=record.started_at,
    )


def _binding_from_json(data: dict[object, object]) -> ContextRunBinding:
    def required_string(key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-blank string")
        return value

    revision = data.get("agent_revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("agent_revision must be an integer")
    try:
        created_at = datetime.fromisoformat(required_string("created_at"))
    except ValueError as exc:
        raise ValueError("created_at must be an ISO-8601 datetime") from exc
    return ContextRunBinding(
        agent_run_id=required_string("agent_run_id"),
        run_id=required_string("run_id"),
        task_id=required_string("task_id"),
        agent_id=required_string("agent_id"),
        agent_revision=revision,
        context_bundle_id=required_string("context_bundle_id"),
        context_bundle_digest=required_string("context_bundle_digest"),
        resolver_version=required_string("resolver_version"),
        policy_version=required_string("policy_version"),
        orchestrator_adapter_id=required_string("orchestrator_adapter_id"),
        created_at=created_at,
    )