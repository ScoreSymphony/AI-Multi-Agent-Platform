"""Safe inspection projections for Context Bundles."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts.types import JsonValue

from .bindings import ContextRunBinding
from .models import ContextBundle, ContextEntry

ContextEntryVisibility = Callable[[ContextEntry], bool]


def context_bundle_projection(
    bundle: ContextBundle,
    *,
    can_view_entry: ContextEntryVisibility | None = None,
    include_inline_content: bool = False,
) -> dict[str, JsonValue]:
    """Project auditable context metadata without making references imply read permission."""

    entries: list[JsonValue] = []
    for entry in bundle.entries:
        visible = can_view_entry(entry) if can_view_entry is not None else False
        item: dict[str, JsonValue] = {
            "ordinal": entry.ordinal,
            "source_type": entry.source.source_type.value,
            "mandatory": entry.mandatory,
            "role": entry.role.value,
            "selection_reason": entry.selection_reason,
            "freshness": entry.freshness.value,
            "data_classification": entry.data_classification.value,
            "estimated_tokens": entry.estimated_tokens,
            "content_bytes": entry.content_bytes,
            "hidden": not visible,
        }
        if visible:
            item.update(
                {
                    "source_id": entry.source.source_id,
                    "source_revision": entry.source.revision,
                    "source_digest": entry.source.digest,
                    "source_snapshot_id": entry.source.snapshot_id,
                    "content_digest": entry.content_digest,
                    "content_ref": entry.content_ref,
                    "inline_content": entry.inline_content if include_inline_content else None,
                    "trust": entry.trust.value,
                    "priority": entry.priority,
                    "relevance": entry.relevance,
                    "project_id": entry.project_id,
                    "workspace_id": entry.workspace_id,
                    "transformation": (
                        None if entry.transformation is None else entry.transformation.to_json()
                    ),
                }
            )
        entries.append(item)

    return {
        "context_bundle_id": bundle.context_bundle_id,
        "digest": bundle.digest,
        "task_id": bundle.task_id,
        "run_id": bundle.run_id,
        "agent_id": bundle.agent_id,
        "agent_revision": bundle.agent_revision,
        "plan_id": bundle.plan_id,
        "step_id": bundle.step_id,
        "skill_bundle_id": bundle.skill_bundle_id,
        "skill_bundle_digest": bundle.skill_bundle_digest,
        "entries": entries,
        "omissions": [
            {
                "source_type": item.source.source_type.value,
                "reason": item.reason.value,
                "mandatory": item.mandatory,
                "detail": item.detail,
            }
            for item in bundle.omissions
        ],
        "budget": bundle.budget.to_json(),
        "usage": bundle.usage.to_json(),
        "resolver_version": bundle.resolver_version,
        "policy_version": bundle.policy_version,
        "created_at": bundle.created_at.isoformat(),
        "reproducibility_limited": bundle.reproducibility_limited,
    }


def context_run_binding_projection(binding: ContextRunBinding) -> dict[str, JsonValue]:
    return binding.to_json()
