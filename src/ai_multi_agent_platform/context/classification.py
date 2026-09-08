"""Canonical effective data-classification helpers for Context Bundles."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    DataClassification,
    strongest_classification,
)

from .models import ContextBundle


def effective_context_bundle_classification(bundle: ContextBundle) -> DataClassification:
    """Return the monotonic effective class of every selected ContextBundle entry.

    Empty bundles are effectively public because they contain no protected outbound data.
    Compatibility vocabulary is normalized through the shared #591 ``DataClassification``
    contract without inspecting or serializing entry content.
    """

    classifications = tuple(
        DataClassification(entry.data_classification.value) for entry in bundle.entries
    )
    return strongest_classification(*classifications) or DataClassification.PUBLIC


__all__ = ["effective_context_bundle_classification"]
