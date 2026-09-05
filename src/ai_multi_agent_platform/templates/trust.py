"""Explicit validation and activation semantics for untrusted Templates.

Trust is revision-local. Imported/untrusted revisions remain immutable and cannot be applied
normally. Activation creates a new published trusted revision with provenance back to the exact
untrusted source revision; it never rewrites the source revision in place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    TemplateProvenance,
    TemplateRevision,
    TemplateRevisionState,
    TemplateTrust,
    utc_now,
)
from .repository import TemplateRepository
from .service import validate_template_configuration


def require_trusted_for_apply(revisions: Iterable[TemplateRevision]) -> None:
    """Reject an apply graph containing any exact untrusted revision."""

    blocked = tuple(
        item
        for item in revisions
        if item.content.provenance.trust is TemplateTrust.UNTRUSTED
    )
    if not blocked:
        return
    raise ContractError(
        ErrorCode.FORBIDDEN,
        "untrusted Template revisions require explicit validation and activation before apply",
        details={
            "untrusted_templates": [
                f"{item.template_id}@{item.revision}" for item in blocked
            ]
        },
    )


def activate_untrusted_revision(
    repository: TemplateRepository,
    template_id: str,
    *,
    expected_revision: int,
    activated_by: OwnerRef,
) -> TemplateRevision:
    """Validate the current untrusted published revision and append a trusted revision.

    Activation is intentionally append-only. The trusted revision carries the same reusable
    configuration intent but records the exact untrusted source in provenance. Compatibility,
    dependency, permission and target-scope checks still run later during preview/apply.
    """

    definition = repository.get_template(template_id)
    if definition.current_revision != expected_revision:
        raise ContractError(
            ErrorCode.CONFLICT,
            "Template changed before trust activation",
            details={
                "expected_revision": expected_revision,
                "current_revision": definition.current_revision,
            },
        )
    source = repository.get_revision(template_id, expected_revision)
    if source.state is not TemplateRevisionState.PUBLISHED:
        raise ContractError(
            ErrorCode.CONFLICT,
            "only a published untrusted Template revision can be activated",
            details={"template_id": template_id, "revision": expected_revision},
        )
    if source.content.provenance.trust is not TemplateTrust.UNTRUSTED:
        raise ContractError(
            ErrorCode.CONFLICT,
            "Template revision does not require trust activation",
            details={
                "template_id": template_id,
                "revision": expected_revision,
                "trust": source.content.provenance.trust.value,
            },
        )

    validate_template_configuration(source.content.configuration)
    now = utc_now()
    next_revision = expected_revision + 1
    source_provenance = source.content.provenance
    metadata = dict(source_provenance.metadata)
    metadata["activated_from_trust"] = source_provenance.trust.value
    activated_content = replace(
        source.content,
        provenance=TemplateProvenance(
            author=activated_by.id,
            source=f"activation:{source.template_id}@{source.revision}",
            trust=TemplateTrust.TRUSTED,
            source_template=source.ref,
            metadata=metadata,
        ),
    )
    activated = replace(
        source,
        revision=next_revision,
        content=activated_content,
        created_at=now,
    )
    updated = replace(
        definition,
        current_revision=next_revision,
        latest_published_revision=next_revision,
        updated_at=now,
    )
    repository.append_revision(updated, activated)
    return activated
