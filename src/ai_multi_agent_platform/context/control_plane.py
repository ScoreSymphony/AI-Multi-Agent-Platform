"""Control Plane projections for canonical Context Bundle evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .bindings import ContextRunBinding, ContextRunBindingRepository
from .classification import effective_context_bundle_classification
from .models import ContextBundle, ContextEntry
from .projection import context_bundle_projection, context_run_binding_projection

CONTEXT_BUNDLE_COLLECTION = "context-bundles"
CONTEXT_RUN_BINDING_COLLECTION = "context-run-bindings"


class ContextBundleReadRepository(Protocol):
    def get(self, context_bundle_id: str) -> ContextBundle: ...
    def list_all(self) -> tuple[ContextBundle, ...]: ...


class ContextEntryVisibilityResolver(Protocol):
    """Per-source viewer permission hook; bundle read permission alone is insufficient."""

    async def can_view(
        self,
        context: RequestContext,
        bundle: ContextBundle,
        entry: ContextEntry,
    ) -> bool: ...


class ContextBundleResourceService:
    """Read-only, value-free northbound Context Bundle inspection."""

    def __init__(
        self,
        repository: ContextBundleReadRepository,
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
        del context
        resources = tuple(self._summary(item) for item in self.repository.list_all())
        return tuple(
            resource
            for resource in resources
            if _matches_filters(resource, query.filters) and _matches_search(resource, query.search)
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        try:
            bundle = self.repository.get(resource_id)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Context Bundle not found: {resource_id}",
            ) from exc
        visible: set[int] = set()
        if self.visibility is not None:
            for entry in bundle.entries:
                if await self.visibility.can_view(context, bundle, entry):
                    visible.add(entry.ordinal)
        projection = context_bundle_projection(
            bundle,
            can_view_entry=lambda item: item.ordinal in visible,
            include_inline_content=False,
            include_omission_details=False,
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
            "plan_id": bundle.plan_id,
            "step_id": bundle.step_id,
            "skill_bundle_id": bundle.skill_bundle_id,
            "skill_bundle_digest": bundle.skill_bundle_digest,
            "source_categories": source_categories,
            "entry_count": len(bundle.entries),
            "omission_count": len(bundle.omissions),
            "budget": bundle.budget.to_json(),
            "usage": bundle.usage.to_json(),
            "resolver_version": bundle.resolver_version,
            "policy_version": bundle.policy_version,
            "created_at": bundle.created_at.isoformat(),
            "reproducibility_limited": bundle.reproducibility_limited,
        }


class ContextRunBindingResourceService:
    """Trace Run/AgentRun identity to the exact immutable Context Bundle evidence."""

    def __init__(
        self,
        repository: ContextRunBindingRepository,
        bundles: ContextBundleReadRepository,
    ) -> None:
        self.repository = repository
        self.bundles = bundles

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        resources = tuple(self._projection(item) for item in self.repository.list_all())
        return tuple(
            resource
            for resource in resources
            if _matches_filters(resource, query.filters) and _matches_search(resource, query.search)
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            binding = self.repository.get(resource_id)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Context Run binding not found: {resource_id}",
            ) from exc
        return self._projection(binding)

    def _projection(self, binding: ContextRunBinding) -> dict[str, JsonValue]:
        bundle = self.bundles.get(binding.context_bundle_id)
        return {
            "id": binding.agent_run_id,
            **context_run_binding_projection(binding),
            "effective_data_classification": effective_context_bundle_classification(bundle).value,
        }


def register_context_control_plane(
    control_plane: ControlPlane,
    bundles: ContextBundleReadRepository,
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


def _matches_filters(
    resource: Mapping[str, JsonValue],
    filters: Mapping[str, str] | None,
) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        value = resource.get(key)
        if isinstance(value, bool):
            actual = "true" if value else "false"
        elif value is None:
            actual = ""
        elif isinstance(value, str | int | float):
            actual = str(value)
        else:
            return False
        if actual != expected:
            return False
    return True


def _matches_search(resource: Mapping[str, JsonValue], search: str | None) -> bool:
    if search is None or not search.strip():
        return True
    needle = search.casefold()
    for key in (
        "id",
        "context_bundle_id",
        "context_bundle_digest",
        "digest",
        "task_id",
        "run_id",
        "agent_id",
        "plan_id",
        "step_id",
        "skill_bundle_id",
    ):
        value = resource.get(key)
        if isinstance(value, str) and needle in value.casefold():
            return True
    return False


__all__ = [
    "CONTEXT_BUNDLE_COLLECTION",
    "CONTEXT_RUN_BINDING_COLLECTION",
    "ContextBundleResourceService",
    "ContextEntryVisibilityResolver",
    "ContextRunBindingResourceService",
    "register_context_control_plane",
]
