"""Single-node classification bridge for Verification-derived Context evidence.

The #86 Verification model intentionally binds exact subject/evidence identity but does not
persist an independent data-classification field. Context must therefore derive classification
from canonical owners without silently weakening sensitive findings.
"""

from __future__ import annotations

from ai_multi_agent_platform.context.models import ContextDataClassification
from ai_multi_agent_platform.context.resolver import ContextSourceRequest
from ai_multi_agent_platform.context.verification_source import (
    VerificationContextClassificationResolver,
)
from ai_multi_agent_platform.contracts import DataClassification, strongest_classification
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext
from ai_multi_agent_platform.verification import VerificationRequest, VerificationResult


class CanonicalVerificationContextClassificationResolver(VerificationContextClassificationResolver):
    """Inherit artifact classifications and fail closed for unclassified Result subjects."""

    def __init__(self, files: FileProvider) -> None:
        self.files = files

    async def classify(
        self,
        source_request: ContextSourceRequest,
        verification_request: VerificationRequest,
        result: VerificationResult,
    ) -> ContextDataClassification:
        classifications: list[DataClassification] = []

        if result.subject.subject_type == "artifact":
            classifications.append(
                await self._artifact_classification(
                    source_request,
                    result.subject.subject_id,
                )
            )
        else:
            # Canonical Result output currently has no persisted owner classification. Do not infer
            # a weaker class from the reviewing Agent, Task or input Context; findings may quote the
            # Result verbatim. Until the Result owner exposes classification, keep it
            # reference-only.
            classifications.append(DataClassification.SECRET)

        for artifact_id in result.evidence_artifact_ids:
            classifications.append(await self._artifact_classification(source_request, artifact_id))

        strongest = strongest_classification(*classifications) or DataClassification.SECRET
        return _context_classification(strongest)

    async def _artifact_classification(
        self,
        request: ContextSourceRequest,
        artifact_id: str,
    ) -> DataClassification:
        access = DataAccessContext(
            operation=request.operation,
            actor_ref=request.actor_ref,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
        )
        linked = [
            record
            for record in await self.files.list_files(access)
            if artifact_id in record.artifact_ids
        ]
        if len(linked) != 1:
            # Missing or ambiguous owner evidence cannot justify rendering reviewer prose inline.
            return DataClassification.SECRET
        return DataClassification(linked[0].classification)


def _context_classification(value: DataClassification) -> ContextDataClassification:
    if value is DataClassification.PUBLIC:
        return ContextDataClassification.PUBLIC
    if value is DataClassification.INTERNAL:
        return ContextDataClassification.INTERNAL
    if value in {DataClassification.CONFIDENTIAL, DataClassification.PRIVATE}:
        return ContextDataClassification.CONFIDENTIAL
    if value is DataClassification.RESTRICTED:
        return ContextDataClassification.RESTRICTED
    return ContextDataClassification.SECRET_REFERENCE


__all__ = ["CanonicalVerificationContextClassificationResolver"]
