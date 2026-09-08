"""Control Plane projections for canonical Context Bundle evidence."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .bindings import ContextRunBinding, ContextRunBindingRepository
from .classification import effective_context_bundle_classification
from .models import ContextBundle, ContextEntry
from .persistence import InMemoryContextBundleRepository
from .projection import context_bundle_projection, context_run_binding_projection

CONTEXT_BUNDLE_COLLECTION = "context-bundles"
CONTEXT_RUN_BINDING_COLLECTION = "context-run-bindings"


class ContextEntryVisibilityResolver(Protocol):
    """Per-source viewer permission hook; bundle read permission alone is insufficient."""

    async def can_view(
        self,
        context: RequestContext,
        bundle: ContextBundle,
        entry: ContextEntry,
    ) -> bool: ...


class ContextBundleResourceService:
    def __init__(
        self,
        repository: InMemoryContextBundleRepository,
        *,
        visibility: ContextEntryVisibilityResolver | None = None,
    ) -> None:
        self.repository = repository
        self.visibility = visibility

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(self._summary(item) for item in self.repository.list_all())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        bundle = self.repository.get(resource_id)
        visible: set[int] = set()
        if self.visibility is not None:
            for entry in bundle.entries:
                if await self.visibility.can_view(context, bundle, entry):
                    visible.add(entry.ordinal)
        projection = context_bundle_projection(
            bundle,
            can_view_entry=lambda item: item.ordinal in visible,
            include_inline_content=False,
        )
        return {"id": bundle.context_bundle_id, **projection}

    @staticmethod
    def _summary(bundle: ContextBundle) -> dict[str, JsonValue]:
        source_categories: list[JsonValue] = [
            category
            for category in sorted({entry.source.source_type.value for entry in bundle.entries})
        ]
        return {
            "id": bundle.context_bundle_id,
            "context_bundle_id": bundle.context_bundle_id,
            "digest": bundle.digest,
            "task_id": bundle.task_id,
            "run_id": bundle.run_id,
            "agent_id": bundle.agent_id,
            "agent_revision": bundle.agent_revision,
            "effective_data_classification": effective_context_bundle_classification(bundle).value,
            "source_categories": source_categories,
            "entry_count": len(bundle.entries),
            "omission_count": len(bundle.omissions),
            "budget": bundle.budget.to_json(),
            "usage": bundle.usage.to_json(),
            "resolver_version": bundle.resolver_version,
            "policy_version": bundle.policy_version,
            "reproducibility_limited": bundle.reproducibility_limited,
        }


class ContextRunBindingResourceService:
    """Project immutable Run bindings with a content-free effective classification summary."""

    def __init__(
        self,
        repository: ContextRunBindingRepository,
        bundles: InMemoryContextBundleRepository,
    ) -> None:
        self.repository = repository
        self.bundles = bundles

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(self._projection(item) for item in self.repository.list_all())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return self._projection(self.repository.get(resource_id))

    def _projection(self, binding: ContextRunBinding) -> dict[str, JsonValue]:
        bundle = self.bundles.get(binding.context_bundle_id)
        return {
            "id": binding.agent_run_id,
            **context_run_binding_projection(binding),
            "effective_data_classification": effective_context_bundle_classification(bundle).value,
        }


def register_context_control_plane(
    control_plane: ControlPlane,
    bundles: InMemoryContextBundleRepository,
    bindings: ContextRunBindingRepository,
    *,
    visibility: ContextEntryVisibilityResolver | None = None,
) -> None:
    control_plane.register_resource_service(
        CONTEXT_BUNDLE_COLLECTION,
        ContextBundleResourceService(bundles, visibility=visibility),
    )
    control_plane.register_resource_service(
        CONTEXT_RUN_BINDING_COLLECTION,
        ContextRunBindingResourceService(bindings, bundles),
    )
