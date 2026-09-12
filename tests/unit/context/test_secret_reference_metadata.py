from __future__ import annotations

import pytest

from ai_multi_agent_platform.context import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
)


def _secret_source() -> ContextSourceRef:
    return ContextSourceRef(
        source_type=ContextSourceType.SYSTEM_SECURITY,
        source_id="secret:database-password",
        revision="secret-ref-r1",
        digest="secret-reference-metadata-digest",
    )


def test_secret_reference_candidate_rejects_freeform_metadata() -> None:
    with pytest.raises(ValueError, match="secret-classified context metadata must be empty"):
        ContextCandidate(
            source=_secret_source(),
            role=ContextEntryRole.CONTEXT,
            selection_reason="authorized secret reference",
            content_ref="secret-ref:database-password",
            content_digest="resolved-secret-content-digest",
            data_classification=ContextDataClassification.SECRET_REFERENCE,
            metadata={"value": "must-never-be-persisted"},
        )


def test_secret_reference_entry_rejects_freeform_metadata() -> None:
    with pytest.raises(ValueError, match="secret-classified context metadata must be empty"):
        ContextEntry(
            ordinal=0,
            source=_secret_source(),
            role=ContextEntryRole.CONTEXT,
            selection_reason="authorized secret reference",
            mandatory=True,
            content_digest="resolved-secret-content-digest",
            content_ref="secret-ref:database-password",
            data_classification=ContextDataClassification.SECRET_REFERENCE,
            metadata={"value": "must-never-be-persisted"},
        )
