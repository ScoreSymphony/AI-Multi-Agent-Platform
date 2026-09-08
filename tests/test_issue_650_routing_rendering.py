from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.context import (
    ContextBudget,
    ContextBundleEgressExporter,
    ContextCandidate,
    ContextEntryRole,
    ContextResolver,
    ContextRoutingPolicy,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
    ReferenceContextOrchestratorAdapter,
    ReferenceContextRenderer,
    merge_context_routing_requirements,
)
from ai_multi_agent_platform.context.operational import _OperationalContextMapper
from ai_multi_agent_platform.context.resolver import ContextAssemblyRequest
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.testing import FakeAuthorizationProvider, FakeModelProvider


class _LocalProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="issue-650-local-provider",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


def _bundle(*, content: str = "canonical local context"):
    task_id = new_id("task")
    candidate = ContextCandidate(
        source=ContextSourceRef(
            source_type=ContextSourceType.TASK,
            source_id=task_id,
            revision="1",
            digest=hashlib.sha256(f"{task_id}:1".encode()).hexdigest(),
        ),
        role=ContextEntryRole.CONTEXT,
        selection_reason="issue 650 routing/rendering regression",
        mandatory=True,
        inline_content=content,
        trust=ContextTrust.TRUSTED,
        relevance=1.0,
    )
    request = ContextAssemblyRequest(
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
        candidates=(candidate,),
        budget=ContextBudget(max_tokens=64_000, max_bytes=256 * 1024, max_items=32),
    )
    return asyncio.run(ContextResolver(FakeAuthorizationProvider()).resolve(request))


def test_context_derived_requirement_rejects_an_undersized_model_route() -> None:
    bundle = _bundle(content="x" * 24_000)
    requirements = merge_context_routing_requirements(
        None,
        bundle,
        policy=ContextRoutingPolicy(output_reserve_tokens=2_048),
    )
    assert requirements.min_context_window is not None
    assert requirements.min_context_window > 4_096

    registry = ModelRegistry()
    registry.register_provider(_LocalProvider())
    registry.register_model(
        ModelConfiguration(
            config_id="issue-650-small-model",
            display_name="Issue 650 Small Model",
            provider_id="issue-650-local-provider",
            location=ModelLocation.LOCAL,
            capabilities=ModelCapabilities(
                context_window=4_096,
                streaming=True,
                modalities=("text",),
            ),
            health=HealthStatus.HEALTHY,
        )
    )

    with pytest.raises(ContractError) as exc_info:
        DeterministicModelRouter(registry).route(requirements)

    assert exc_info.value.code is ErrorCode.NO_COMPATIBLE_ROUTE


def test_local_reference_rendering_keeps_bundle_identity_and_requires_no_egress() -> None:
    bundle = _bundle(content="local context remains local")

    rendered = asyncio.run(ReferenceContextRenderer().render(bundle))

    assert rendered.context_bundle_id == bundle.context_bundle_id
    assert rendered.context_bundle_digest == bundle.digest
    assert len(rendered.parts) == len(bundle.entries) == 1
    assert rendered.parts[0].content == "local context remains local"


def test_context_bound_mapping_rejects_legacy_task_or_project_context_mixing() -> None:
    bundle = _bundle()
    operation = OperationContext(
        correlation_id=bundle.task_id,
        owner_type="user",
        owner_id="issue-650",
    )
    mapper = _OperationalContextMapper(
        bundle=bundle,
        adapter=ReferenceContextOrchestratorAdapter(),
        renderer=ReferenceContextRenderer(),
        exporter=ContextBundleEgressExporter(),
        operation=operation,
        target_resolver=None,
        content_provider=None,
    )
    legacy_spec = SimpleNamespace(
        task_id=bundle.task_id,
        run_id=bundle.run_id,
        agent_revision=SimpleNamespace(
            agent_id=bundle.agent_id,
            revision=bundle.agent_revision,
        ),
        task_context={"legacy": "must-not-be-mixed"},
        project_context={},
    )

    with pytest.raises(ValueError, match="cannot mix canonical Context Bundle"):
        asyncio.run(mapper.map_agent(legacy_spec))  # type: ignore[arg-type]
